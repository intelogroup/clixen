"""observe / act: the orb's screen tools. Numbered elements, a measured effect after every action, typed errors.

`observe` lists what can be acted on, each with an id valid for that observation. `act` takes an id (or a label),
re-reads the screen, acts, re-reads again and reports what changed. The agent never has to guess whether a click
worked; the engine measured it.
"""
import difflib
import json
import re
import time
from pathlib import Path
from collections import Counter

from tools import ax_context as ax
from tools import screen_act

MAX_ITEMS = 40
TRACE_FILE = Path.home() / ".config" / "g4l" / "data" / "screen_v2.jsonl"  # full args and results: the run traces keep only 120 characters
_TRACE_MAX = 2_000_000
_LABEL_MAX = 80
_EDITABLE = {"TextField", "TextArea", "SearchField", "ComboBox"}
_EXPECT_TRIES = 10  # x 0.4s: how long `expect` waits for the screen to show the result
_FAST_MAX_AGE = 30.0  # s: an id this fresh is checked with a hit-test, not a new walk of the whole tree
_HISTORY = 12  # observations whose ids can still be acted on
_state = {"rev": 0, "hist": {}, "counts": Counter(), "title": "", "t": 0.0}  # hist: rev -> {id -> {"fp": (role, label, nth), x, y}}


def _focused() -> tuple[str, str]:
    """(role, label) of the keyboard-focused element ('', '' when unreadable)."""
    try:
        f = screen_act._focus()
        return ((f[0] or "")[2:] if f and f[0] else "", screen_act._focus_label())
    except Exception:
        return "", ""


def _value(e: dict) -> str:
    try:
        v = ax._get(e["el"], "AXValue")
        return "" if v is None else str(v)
    except Exception:
        return ""


def _snapshot(app: str = "", query: str = ""):
    """(title, all elements, ranked items). Page content before browser chrome, then reading order; capped."""
    name, title, els = ax.elements(app, query)
    _state["app"] = name
    ranked = sorted(els, key=lambda e: (e.get("panel", False), not e["web"], e["y"], e["x"]))  # the page first, browser chrome next, an extension panel last
    seen: Counter = Counter()
    items = []
    for e in ranked[:MAX_ITEMS]:
        label = e["label"][:_LABEL_MAX]
        key = (e["role"], label)
        items.append({"e": e, "fp": (e["role"], label, seen[key])})
        seen[key] += 1
    return title, els, items


def _register(items: list, title: str = "") -> int:
    """Store this observation under a new rev. Ids read e<n>.<rev>, so an id always means the element it named."""
    rev = _state["rev"] + 1
    _state["rev"] = rev
    _state["hist"][rev] = {f"e{i + 1}.{rev}": {"fp": it["fp"], "x": it["e"]["x"], "y": it["e"]["y"]} for i, it in enumerate(items)}
    _state["hist"].pop(rev - _HISTORY, None)
    _state.update(counts=Counter(it["fp"][:2] for it in items), title=title, t=time.time())
    return rev


def _id(i: int) -> str:
    return f"e{i + 1}.{_state['rev']}"


def _trace(tool: str, args: dict, result: str) -> str:
    try:
        TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if TRACE_FILE.exists() and TRACE_FILE.stat().st_size > _TRACE_MAX:
            TRACE_FILE.write_text("")
        with TRACE_FILE.open("a") as f:
            f.write(json.dumps({"t": round(time.time(), 1), "tool": tool, "args": args, "result": result}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return result


def _out(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False)


def _err(code: str, hint: str, **extra) -> str:
    return _out({"ok": False, "rev": _state["rev"], "error": {"code": code, "hint": hint, **extra}})


def _overlays(els: list, app: str) -> list[str]:
    out = ["extension side panel"] if ax.side_panel_open(els) else []
    return out + [f"popover: {p}" for p in ax.popovers(app)]


def observe(app: str = "", query: str = "") -> str:
    return _trace("observe", {"app": app, "query": query}, _observe(app, query))


def _observe(app: str, query: str) -> str:
    title, els, items = _snapshot(app, query)
    rev = _register(items, title)
    role, label = _focused()
    focus_id = next((_id(i) for i, it in enumerate(items) if it["fp"][0] == role and it["fp"][1] == label[:_LABEL_MAX]), None) if label else None
    rows = []
    for i, it in enumerate(items):
        row = {"id": _id(i), "role": it["fp"][0], "label": it["fp"][1]}
        if it["fp"][0] in _EDITABLE | {"CheckBox", "RadioButton"}:
            v = _value(it["e"])
            if v:
                row["value"] = v[-_LABEL_MAX:]
        rows.append(row)
    return _out({"rev": rev, "app": _state.get("app") or app, "title": title, "url": ax.front_url(app),
                 "focus": {"id": focus_id, "role": role, "label": label[:_LABEL_MAX]},
                 "overlays": _overlays(els, app), "elements": rows})


_ID = re.compile(r"e\d+\.(\d+)")


def _lookup(t: str) -> dict | None:
    m = _ID.fullmatch(t)
    return _state["hist"].get(int(m.group(1)), {}).get(t) if m else None


def _resolve(target: str, items: list):
    """(index, None) or (None, (code, hint, extra)). Candidate ids belong to the next rev: the caller registers `items` on an error."""
    t = target.strip()
    nxt = _state["rev"] + 1
    if _ID.fullmatch(t):
        old = _lookup(t)
        if old is None:
            return None, ("unknown_id", f"{t} is not from a recent observation; call observe.", {})
        for i, it in enumerate(items):
            if it["fp"] == old["fp"]:
                return i, None
        return None, ("stale", f"{t} ({old['fp'][0]} '{old['fp'][1]}') is no longer on screen; the page changed. Call observe.", {})
    want = t.lower()
    exact = [i for i, it in enumerate(items) if it["fp"][1].lower() == want]
    pool = exact or [i for i, it in enumerate(items) if want in it["fp"][1].lower()]
    if len(pool) == 1:
        return pool[0], None
    if pool:
        cands = [{"id": f"e{i + 1}.{nxt}", "role": items[i]["fp"][0], "label": items[i]["fp"][1]} for i in pool[:5]]
        return None, ("ambiguous", f"'{t}' matches {len(pool)} elements; act on one by id.", {"candidates": cands})
    labels = [it["fp"][1] for it in items]
    close = difflib.get_close_matches(want, [l.lower() for l in labels], n=3, cutoff=0.4)
    cands = [{"id": f"e{j + 1}.{nxt}", "role": items[j]["fp"][0], "label": items[j]["fp"][1]} for j, l in enumerate(labels) if l.lower() in close][:3]
    return None, ("not_found", f"nothing on screen is labelled '{t}'. Call observe to see what is there.", {"candidates": cands})


def _met(expect: dict, title: str, items: list) -> tuple[bool, str]:
    labels = [it["fp"][1].lower() for it in items]
    if (w := expect.get("appears")) and not any(w.lower() in l for l in labels + [title.lower()]):  # a result often shows only in the window title
        return False, f"'{w}' has not appeared"
    if (w := expect.get("gone")) and any(w.lower() in l for l in labels):
        return False, f"'{w}' is still there"
    if (w := expect.get("title_has")) and w.lower() not in title.lower():
        return False, f"title is '{title}', lacking '{w}'"
    if (w := expect.get("focus_role")) and _focused()[0].lower() != w.lower():
        return False, f"focus is on a {_focused()[0] or 'nothing'}, not a {w}"
    if (w := expect.get("value_contains")):
        f = screen_act._focus()
        if not (f and w.lower() in (f[1] or "").lower()):
            return False, f"the focused field does not contain '{w}'"
    return True, ""


def _holds(old: dict) -> bool:
    """True when the point stored for an element still sits on an element with that label (cheap hit-test)."""
    want = old["fp"][1].lower()[:40]
    if not want:
        return False
    try:
        return any(want in lab.lower() or lab.lower() in want for lab in ax.labels_at(old["x"], old["y"]) if lab)
    except Exception:
        return False


def _act(target: str, action: str, text: str, expect: dict | None, app: str, submit: bool) -> str:
    if (why := screen_act._guard(typing=action == "type")):
        return _err("refused", why)
    t = target.strip()
    role = label = ""
    cx = cy = 0
    cleared: list[str] = []
    old = _lookup(t)
    latest = bool(old) and int(_ID.fullmatch(t).group(1)) == _state["rev"]  # the fast path needs the observation the agent last saw
    if action in ("click", "type") and old and latest and time.time() - _state["t"] <= _FAST_MAX_AGE and _holds(old):
        role, label, cx, cy = old["fp"][0], old["fp"][1], old["x"], old["y"]  # fast path: no walk before the action
        before, title, focus_before = _state["counts"], _state["title"], _focused()
        if action == "type" and role not in _EDITABLE:
            return _err("not_editable", f"'{label}' is a {role}, not a text field.")
    else:
        title, els, items = _snapshot(app)
        if ax.side_panel_open(els) and "close side panel" not in t.lower():
            if screen_act.close_side_panel(app):  # an open extension panel squeezes the page: close it first, say so
                cleared.append("extension side panel")
                title, els, items = _snapshot(app)
        if action in ("click", "type"):
            idx, bad = _resolve(target, items)
            if bad:
                _register(items, title)  # the candidate ids in the error point at this walk
                return _err(bad[0], bad[1], **bad[2])
            e = items[idx]["e"]
            role, label, cx, cy = items[idx]["fp"][0], items[idx]["fp"][1], e["x"], e["y"]
            if action == "type" and role not in _EDITABLE:
                return _err("not_editable", f"'{label}' is a {role}, not a text field.")
        before, focus_before = Counter(it["fp"][:2] for it in items), _focused()
    note = ""
    if action in ("click", "type"):
        screen_act._click(cx, cy)
        if action == "type":
            for _ in range(10):  # the click returns before the browser moves focus into the field: typing early reads the page as focused
                if _focused()[0] in _EDITABLE:
                    break
                time.sleep(0.08)
            note = screen_act.type_text(text, submit)
            if note.startswith("[screen error]"):
                return _err("type_failed", note)
    elif action == "key":
        note = screen_act.press_keys(text, app)
        if note.startswith("[screen error]"):
            return _err("key_failed", note)
    elif action == "scroll":
        note = screen_act.scroll_page(text or "down", 1, app)
        if note.startswith("[screen error]"):
            return _err("scroll_failed", note)
    else:
        return _err("bad_action", f"action must be click, type, key or scroll, not '{action}'.")
    time.sleep(0.15)
    title2, _, items2 = _snapshot(app)
    verdict, why = True, ""
    title_only = bool(expect) and set(expect) == {"title_has"}
    for attempt in range(_EXPECT_TRIES if expect else 1):
        verdict, why = _met(expect or {}, title2, items2)
        if verdict or attempt == _EXPECT_TRIES - 1:
            break
        time.sleep(0.4)
        if title_only:
            title2 = ax.window_title(app)  # cheap: no tree walk while we only wait on the title
        else:
            title2, _, items2 = _snapshot(app)
    if title_only:
        title2, _, items2 = _snapshot(app)  # one full read for the effect report
    rev = _register(items2, title2)
    a = _state["counts"]
    appeared = [l for (_, l) in (a - before) if l][:8]
    gone = [l for (_, l) in (before - a) if l][:8]
    focus_after = _focused()
    effect = {"focus": focus_after[1][:_LABEL_MAX] or None, "title": title2 if title2 != title else None, "appeared": appeared, "gone": gone}
    if note:
        effect["note"] = note[:160]
    out = {"ok": True, "rev": rev, "effect": effect}
    if cleared:
        out["cleared"] = cleared
    if expect:
        if not verdict:
            return _out({"ok": False, "rev": rev, "effect": effect, **({"cleared": cleared} if cleared else {}),
                         "performed": True,
                         "error": {"code": "expect_failed", "hint": f"{why}. The action itself ran: do not repeat it. Observe to see what the screen shows now."}})
        out["expect"] = "met"
    if action == "click" and not (appeared or gone or effect["title"] or focus_after != focus_before):
        out["warning"] = "no_effect"
    return _out(out)


def act(target: str = "", action: str = "click", text: str = "", expect: dict | None = None, app: str = "", submit: bool = False,
        steps: list | None = None) -> str:
    """One action, or `steps`: several actions run in order in one call, stopping at the first failure."""
    if steps:
        results = []
        for st in steps[:12]:
            r = json.loads(_act(str(st.get("target", "")), st.get("action", "click"), str(st.get("text", "")),
                                st.get("expect") if isinstance(st.get("expect"), dict) else None, app, bool(st.get("submit"))))
            results.append(r)
            if not r.get("ok"):
                break
        out = _out({"ok": all(r.get("ok") for r in results) and len(results) == len(steps), "done": len(results), "of": len(steps),
                    "rev": _state["rev"], "results": results})
        return _trace("act", {"steps": steps[:12], "app": app}, out)
    args = {"target": target, "action": action, "text": text[:200], "expect": expect, "submit": submit}
    return _trace("act", args, _act(target, action, text, expect, app, submit))


OBSERVE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "observe",
        "description": (
            "Looks at the user's screen and returns what you can act on: the app, page title and URL, what has keyboard focus, "
            "any overlay in the way (an extension side panel, a popover), and up to 40 numbered elements (id, role, label, and the "
            "current value of text fields). Page content comes first. Ids stay valid for a few observations; a stale one is rejected, never mis-clicked. Call it before "
            "acting and whenever you are unsure what the screen shows. query keeps only elements whose label contains it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "App name, e.g. 'Google Chrome'; omit for the front app."},
                "query": {"type": "string", "description": "Only elements whose label contains this text."},
            },
        },
    },
}

ACT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "act",
        "description": (
            "Does one thing on the user's screen (or a batch via steps) and measures what changed. target: an element id from observe, copied exactly (e.g. 'e7.3'), or "
            "its label. action: 'click', 'type' (clicks the field, then types text; submit=true presses Return), 'key' (text is a "
            "shortcut like 'cmd+return' or 'esc'), or 'scroll' (text is 'up'/'down'). Returns {ok, effect:{focus, title, appeared, "
            "gone}} or {ok:false, error:{code, hint, candidates}}; a click that changed nothing carries warning 'no_effect'. "
            "Extension side panels are closed for you. Pass expect to wait for a result, e.g. {\"appears\": \"Message sent\"}, "
            "{\"title_has\": \"...\"}, {\"gone\": \"...\"}, {\"focus_role\": \"TextField\"} or {\"value_contains\": \"...\"}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Element id from observe, or its label."},
                "action": {"type": "string", "enum": ["click", "type", "key", "scroll"]},
                "text": {"type": "string", "description": "Text to type, shortcut to press, or scroll direction."},
                "submit": {"type": "boolean", "description": "With action 'type': press Return after typing."},
                "expect": {"type": "object", "description": "Condition to wait for after the action."},
                "app": {"type": "string", "description": "App name; omit for the front app."},
                "steps": {
                    "type": "array",
                    "description": (
                        "Several actions in ONE call, run in order, stopping at the first failure: each {target, action, text, expect}. "
                        "Use it to fill a whole form or click a short sequence without a round trip per field. Ids from one observe stay valid."
                    ),
                    "items": {"type": "object"},
                },
            },
        },
    },
}
