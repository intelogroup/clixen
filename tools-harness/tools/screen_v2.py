"""observe / act: the orb's screen tools. Numbered elements, a measured effect after every action, typed errors.

`observe` lists what can be acted on, each with an id valid for that observation. `act` takes an id (or a label),
re-reads the screen, acts, re-reads again and reports what changed. The agent never has to guess whether a click
worked; the engine measured it.
"""
import difflib
import json
import re
import subprocess
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
    """(title, all elements, ranked items). Page content before browser chrome, then reading order; capped.

    Asks for offscreen elements too. One walk either way — the tree is already built —
    and it is the only way to tell "this page has nothing" apart from "this page has
    nothing *in view*", which is the difference between scrolling and giving up.
    """
    name, title, els = ax.elements(app, query, offscreen=True)
    _state["app"] = name
    # Keep the off-screen rows by name. Counting them (fold.below) told the model there
    # was more page, but not *which field* to scroll to, so it still reported a field
    # below the fold as "not found" instead of scrolling to it.
    below = []
    for e in els:
        if e.get("onscreen", True):
            continue
        role, label = e.get("role", ""), (e.get("label") or "")[:_LABEL_MAX]
        if label and (role, label) not in below:
            below.append((role, label))
    _state["below"] = below[:40]
    visible = [e for e in els if e.get("onscreen", True)]
    ranked = sorted(visible, key=lambda e: (e.get("panel", False), not e["web"], e["y"], e["x"]))  # the page first, browser chrome next, an extension panel last
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


# A consent banner is not an AX "popover": it lives inside the page, so the browser
# reports no popover and observe() used to say overlays: []. Measured on
# theguardian.com 2026-10-25: the page was blocked by a CMP whose focused WebArea
# was "Privacy Manager US App", the AX tree exposed only "Closer" and "Do not sell
# or share my personal information" (the real Accept/Reject live in an iframe), and
# nothing in the payload said a dialog was in the way.
_BANNER_WORDS = (
    "privacy manager", "consent", "cookie", "we use cookies", "gdpr",
    "accept all", "reject all", "cookie settings", "before you continue",
)
_BANNER_ACCEPT = ("accept all", "accept cookies", "i agree", "agree and continue", "accept")
_BANNER_REJECT = ("reject all", "reject", "decline", "do not sell", "don't sell", "opt out")
_BANNER_DISMISS = ("closer", "close", "dismiss", "got it", "manage choices", "ok, thanks")


def _blocking_banner(focus_label: str, labels: list[str]) -> str:
    """Describe a page-level consent/dialog that is in the way, or ''.

    The distinction between accept / reject / dismiss is not cosmetic: clicking
    "accept" or "do not sell" is a privacy election made on the user's behalf, so
    the message says which choices are actually present and never implies that any
    of them is the agent's to make.
    """
    haystack = " ".join([focus_label or ""] + [label or "" for label in labels]).lower()
    if not any(word in haystack for word in _BANNER_WORDS):
        return ""
    where = f"focus is '{focus_label[:60]}'" if focus_label else "page text mentions consent"
    accept = [lb for lb in labels if any(a == (lb or "").lower().strip() or a in (lb or "").lower() for a in _BANNER_ACCEPT)]
    reject = [lb for lb in labels if any(r in (lb or "").lower() for r in _BANNER_REJECT)]
    dismiss = [lb for lb in labels if (lb or "").lower().strip() in _BANNER_DISMISS]

    if accept or reject:
        parts = [x for x in (
            f"accept: {', '.join(accept[:2])}" if accept else "",
            f"reject: {', '.join(reject[:2])}" if reject else "",
            f"dismiss: {', '.join(dismiss[:2])}" if dismiss else "",
        ) if x]
        return (f"consent banner ({where}); choices present — {' / '.join(parts)}. Choosing "
                "accept or reject is the user's decision: dismiss it or ask, never elect for them")
    if dismiss:
        return (f"consent banner ({where}); only a dismiss control is exposed "
                f"({', '.join(dismiss[:2])}) — closing it is neutral, and the banner has no "
                "accept/opt-out in the element list, so do not claim either was chosen")
    return (
        f"consent banner ({where}) — its buttons are NOT in the element list (usually an "
        "iframe): try click_text, or read the page first"
    )


def _visual_check(question: str) -> str:
    """Ask the vision model what the front window actually shows.

    Why: the accessibility tree is text, and text loses the things that most often
    cause a wrong click — which of two near-identical rows is the one wanted, whether
    a control is greyed out, what an icon means. Measured failure this prevents: the
    orb clicked "IGM Vol 04 No 48" when the ask was No 44, because the labels differed
    by two digits and nothing in the AX text said which was which.

    Costs one model call (~1.5 s) and only runs when asked.
    """
    try:
        from tools.vision_analyze import analyze_image

        return analyze_image(question, region="front_window")
    except Exception as exc:  # noqa: BLE001 - never fail the observation for this
        return f"[visual check unavailable: {type(exc).__name__}: {exc}]"


def find_on_page(text: str = "", max_scrolls: int = 12, app: str = "") -> str:
    """Scroll the page until `text` is on screen; say so if it never is.

    Why this exists: the accessibility tree only contains what is in the viewport, and
    Chrome reports no scroll extent, so an agent cannot tell "this page has no 2025
    issues" from "they are below the fold". Measured 2026-10-25: the orb concluded a
    journal archive had no January 2025 issue, twice, while the cards were one scroll
    away. Absence has to be earned by reaching the bottom, not assumed from one look.

    Deliberately mechanical: observe, scroll a page, observe again, and stop when the
    labels stop changing (that is the bottom). It reports how many scrolls it took, so
    the caller knows whether the answer was near or far.
    """
    want = (text or "").strip().lower()
    if not want:
        return "[error] find_on_page needs the text to look for"

    tries = max(1, min(int(max_scrolls or 12), 30))
    last_labels: set[str] = set()
    for attempt in range(tries + 1):
        title, els, items = _snapshot(app)
        for i, it in enumerate(items):
            if want in it["fp"][1].lower():
                # Put it in the middle of the window so a click lands where it looks.
                try:
                    ax.scroll_into_view(it["e"]["el"])
                except Exception:  # noqa: BLE001 - finding it is the job, centring is a bonus
                    pass
                how = "already on screen" if attempt == 0 else f"after scrolling {attempt} page(s)"
                return _out({
                    "found": True,
                    "label": it["fp"][1],
                    "role": it["fp"][0],
                    "id": _id(i),
                    "note": f"'{it['fp'][1]}' is {how}.",
                })
        labels = {it["fp"][1] for it in items}
        if attempt and labels == last_labels:
            # Reached the bottom. Before saying "not here", name the controls that
            # could be hiding it: a period/category filter is the usual way an
            # archive keeps last year's issues out of view entirely.
            filters = [
                {"id": _id(i), "role": it["fp"][0], "label": it["fp"][1]}
                for i, it in enumerate(items)
                if it["fp"][0] in ("PopUpButton", "ComboBox", "MenuButton", "Button")
                and any(k in it["fp"][1].lower() for k in
                        ("période", "periode", "period", "catégor", "categor", "filtr",
                         "année", "annee", "year", "mois", "month", "trier", "sort"))
            ][:6]
            payload = {
                "found": False,
                "reason": "reached the bottom of the page: the view stopped changing",
                "scrolls": attempt,
                "note": f"'{text}' is not on this page as far as scrolling can tell.",
            }
            if filters:
                payload["filters"] = filters
                payload["hint"] = ("it may be hidden behind a filter: click one of these and "
                                   "observe again before saying it does not exist")
            return _out(payload)
        # Two identical viewports in a row means the page is not moving; handled above.
        last_labels = labels
        if attempt == tries:
            break
        screen_act.scroll_page("down", 1, app)
        time.sleep(0.45)
    return _out({
        "found": False,
        "reason": f"not found in {tries} scroll(s); more may exist further down",
        "scrolls": tries,
        "hint": "call again with a larger max_scrolls, or use scrapling_api_capture to read the data directly",
    })


def observe(app: str = "", query: str = "", visual: bool = False,
            visual_question: str = "") -> str:
    return _trace(
        "observe",
        {"app": app, "query": query, "visual": visual},
        _observe(app, query, visual, visual_question),
    )


def _observe(app: str, query: str, visual: bool = False, visual_question: str = "") -> str:
    title, els, items = _snapshot(app, query)
    rev = _register(items, title)
    # Record what the model just saw. click_text/type_text have no element ids to
    # anchor on, so this snapshot is what lets them refuse to type into a
    # different app than the one that was actually looked at.
    from tools import screen_epoch

    screen_epoch.note_look(_state.get("app") or app, title)
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
    labels = [it["fp"][1] for it in items]
    overlays = _overlays(els, app)
    if banner := _blocking_banner(label, labels):
        overlays.append(banner)
    payload = {"rev": rev, "app": _state.get("app") or app, "title": title, "url": ax.front_url(app),
               "focus": {"id": focus_id, "role": role, "label": label[:_LABEL_MAX]},
               "overlays": overlays, "elements": rows}
    below = sum(1 for e in els if e.get("below"))
    above = sum(1 for e in els if e.get("above"))
    # Chrome does not always set `below` on an off-screen row (below-fold fields often
    # report height 0), so the count alone under-reports. Any off-screen row is enough to
    # say "there is more page here" — which is the signal to scroll, not to conclude absence.
    offscreen = _state.get("below", [])
    if offscreen:
        below = max(below, len(offscreen))
    if below or above or offscreen:
        # The actionable thing this turn: there is more page than you can see, so
        # "not on screen" is not yet "not there". Named so the next step is scrolling,
        # not a search box.
        named = [label for (_role, label) in _state.get("below", []) if label][:10]
        payload["fold"] = {
            "below": below,
            "above": above,
            "below_labels": named,
            "hint": ("these are off screen, not missing. To reach one, call find_on_page(label) — "
                     "it scrolls until the label is visible and centres it — then act on it. "
                     "Or act(action='scroll', text='down') and observe again. Do not click or "
                     "type at coordinates outside the viewport."),
        }
    out = _out(payload)
    if visual:
        # Corroborate the text reading against the pixels. The element list says what
        # is *there*; the screenshot says what it *looks like* — which is what decides
        # between two rows whose labels differ by a digit, or whether a control is
        # actually disabled. Wrapped like any external content: a screen can carry text
        # that is trying to instruct the agent.
        q = visual_question or (
            "Describe what this window shows in one or two sentences. Then list the "
            "distinct clickable labels you can actually read, in order, exactly as they "
            "appear — especially any numbers, dates or volume/issue identifiers. Do not "
            "guess a label you cannot read."
        )
        answer = _visual_check(q)
        try:
            from tools.injection_guard import wrap_external_output

            answer = wrap_external_output("analyze_image", answer)
        except Exception:  # noqa: BLE001 - the guard is defence in depth, not a dependency
            pass
        out = json.dumps({**json.loads(out), "visual": answer})
    return out


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
    # "I can't see it" must not read as "it isn't there". If the label exists below the
    # fold, say so and name the next step; a generic not_found makes the model conclude
    # the form has no such field.
    offscreen = [label for (_role, label) in _state.get("below", [])]
    hit = next((lab for lab in offscreen if lab.lower() == want or want in lab.lower()), None)
    if hit:
        return None, (
            "below_viewport",
            f"'{hit}' exists but is below the viewport, so it cannot be clicked or typed into "
            f"yet. Call find_on_page('{hit[:40]}') to scroll it into view, then act on the id "
            "from the fresh observation.",
            {"candidates": []},
        )
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


def _act(target: str, action: str, text: str, expect: dict | None, app: str, submit: bool, replace: bool = False) -> str:
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
            if replace:  # correcting a field: select what is there so the new text replaces it instead of piling on
                screen_act.press_keys("cmd+a", app)
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
        steps: list | None = None, replace: bool = False) -> str:
    """One action, or `steps`: several actions run in order in one call, stopping at the first failure."""
    if steps:
        results = []
        for st in steps[:12]:
            r = json.loads(_act(str(st.get("target", "")), st.get("action", "click"), str(st.get("text", "")),
                                st.get("expect") if isinstance(st.get("expect"), dict) else None, app, bool(st.get("submit")), bool(st.get("replace"))))
            results.append(r)
            if not r.get("ok"):
                break
        out = _out({"ok": all(r.get("ok") for r in results) and len(results) == len(steps), "done": len(results), "of": len(steps),
                    "rev": _state["rev"], "results": results})
        return _trace("act", {"steps": steps[:12], "app": app}, out)
    args = {"target": target, "action": action, "text": text[:200], "expect": expect, "submit": submit}
    return _trace("act", args, _act(target, action, text, expect, app, submit, replace))


def wait_for(expect: dict | None = None, timeout: float = 8.0, app: str = "") -> str:
    """Poll the screen until `expect` holds (same conditions as act's expect) or `timeout` seconds pass."""
    expect = expect or {}
    start = time.monotonic()
    deadline = start + max(0.5, min(float(timeout), 30.0))
    while True:
        title, _, items = _snapshot(app)
        met, why = _met(expect, title, items)
        if met:
            return _out({"ok": True, "met": True, "waited": round(time.monotonic() - start, 1), "title": title})
        if time.monotonic() >= deadline:
            return _out({"ok": False, "error": {"code": "timeout", "hint": f"{why} after {round(time.monotonic() - start, 1)}s. Page title: '{title}'."}})
        time.sleep(0.3)


_CHROMES = {"google chrome", "chromium", "brave browser", "microsoft edge"}
_SAFARI = {"safari"}


def _family(app: str) -> str:
    """Which AppleScript dialect `tabs` must speak: 'chrome' or 'safari'.

    Safari is not a second tool, it is a second dictionary: tabs expose `name`
    where Chrome exposes `title`, and switching is `current tab of window` where
    Chrome takes `active tab index`. Everything else — the guard, the payload
    shape, the errors — is shared, which is the point of one browser layer.
    """
    low = (app or "").lower()
    if low in _CHROMES:
        return "chrome"
    if low in _SAFARI:
        return "safari"
    return ""


def _list_script(app: str, family: str) -> str:
    prop = "name" if family == "safari" else "title"
    return (
        f'tell application "{app}"\nset out to ""\n'
        "repeat with wi from 1 to (count of windows)\n"
        "repeat with ti from 1 to (count of tabs of window wi)\n"
        f'set out to out & wi & "|" & ti & "|" & ({prop} of tab ti of window wi)'
        ' & "|" & (URL of tab ti of window wi) & linefeed\n'
        "end repeat\nend repeat\nreturn out\nend tell"
    )


def _switch_script(app: str, family: str, window: int, index: int) -> str:
    if family == "safari":
        return (
            f'tell application "{app}"\n'
            f"set current tab of window {window} to tab {index} of window {window}\n"
            f"set index of window {window} to 1\nactivate\nend tell"
        )
    return (
        f'tell application "{app}"\n'
        f"set active tab index of window {window} to {index}\n"
        f"set index of window {window} to 1\nactivate\nend tell"
    )


def _chrome_instances() -> list[str]:
    """Distinct --user-data-dir values of running Chrome processes.

    Two Chrome instances (a normal profile plus an agent profile with its own
    --user-data-dir) both answer to the bundle id, but AppleScript addresses only
    ONE of them while AX sees whichever window is actually on screen. Measured
    2026-10-25: `tabs` listed the exam tab from the agent profile while the window
    in front was a CNN article in the other profile — the agent concluded the page
    had not opened and started retrying.
    """
    try:
        out = subprocess.run(
            ["ps", "-Ao", "args"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return []
    dirs = set()
    for line in out.splitlines():
        if "Google Chrome.app/Contents/MacOS/Google Chrome" not in line:
            continue
        for token in line.split():
            if token.startswith("--user-data-dir="):
                dirs.add(token.split("=", 1)[1])
    return sorted(dirs)


def tabs(action: str = "list", match: str = "", app: str = "Google Chrome") -> str:
    """List, switch to, or close browser tabs by a title or URL fragment.

    Works for the Chrome family and for Safari: same tool, same payload, different
    AppleScript dialect (see `_family`). One browser layer with an `app=` argument,
    never a per-browser tool family.
    """
    family = _family(app)
    if not family:
        return _err(
            "unsupported_app",
            "tabs works with Chrome-family browsers (Chrome, Chromium, Brave, Edge) and "
            f"Safari, not '{app}'.",
        )
    r = subprocess.run(["osascript", "-e", _list_script(app, family)],
                       capture_output=True, text=True, timeout=15)
    rows = []
    for line in r.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            title, _, url = parts[2].rpartition("|")
            rows.append({"window": int(parts[0]), "index": int(parts[1]), "title": title, "url": url})

    # Which browser is on screen? This is the instance the user (and the bunny) is
    # watching, so a tab list that excludes it is misleading rather than useful.
    front_title = ""
    try:
        from tools import ax_context as _ax

        front_title = _ax.window_title(app) or ""
    except Exception:
        front_title = ""

    def _overlaps(tab_title: str) -> bool:
        words = {w for w in front_title.lower().replace("—", " ").split() if len(w) > 3}
        return any(w in tab_title.lower() for w in words) if words else True

    on_screen = not front_title or any(_overlaps(t["title"]) for t in rows)
    instances = _chrome_instances() if family == "chrome" else []
    note = ""
    if not on_screen:
        note = (
            f"another {app} instance is in front ({front_title[:60]!r}) and AppleScript "
            f"enumerated a different one"
            + (f"; {len(instances)} Chrome profiles are running" if len(instances) > 1 else "")
            + ". Use observe/read_page_text for the page you can see; tabs describes the "
              "other instance."
        )

    if action == "list":
        payload = {"ok": True, "tabs": rows}
        if note:
            payload.update({"on_screen": False, "front_window_title": front_title[:120], "note": note})
        return _out(payload)
    if not on_screen:
        # Act on a browser the user cannot see and a wrong click becomes a mystery:
        # refuse instead of switching/closing tabs in the invisible instance.
        return _err("wrong_instance", note)
    if action not in ("switch", "close"):
        return _err("bad_action", "action must be list, switch or close.")
    if not match.strip():
        return _err("need_match", f"{action} needs match: part of a tab's title or URL.")
    hits = [t for t in rows if match.lower() in t["title"].lower() or match.lower() in t["url"].lower()]
    if not hits:
        return _err("not_found", f"no open tab matches '{match}'.", candidates=[t["title"][:50] for t in rows][:8])
    if action == "switch":
        t = hits[0]
        subprocess.run(["osascript", "-e", _switch_script(app, family, t["window"], t["index"])],
                       capture_output=True, text=True, timeout=15)
        return _out({"ok": True, "switched_to": t["title"], "url": t["url"]})
    if len(hits) > 8:
        return _err("too_many", f"'{match}' matches {len(hits)} tabs; use a more specific match.")
    for t in sorted(hits, key=lambda t: (t["window"], t["index"]), reverse=True):  # last first, so the indexes of the rest stay valid
        subprocess.run(["osascript", "-e", f'tell application "{app}" to close tab {t["index"]} of window {t["window"]}'], capture_output=True, text=True, timeout=15)
    return _out({"ok": True, "closed": [t["title"] for t in sorted(hits, key=lambda t: (t["window"], t["index"]))]})


WAIT_FOR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "wait_for",
        "description": (
            "Waits until the screen shows something, then returns. expect uses the same conditions as act: {\"appears\": \"text\"}, "
            "{\"gone\": \"text\"}, {\"title_has\": \"text\"}, {\"focus_role\": \"TextField\"}, {\"value_contains\": \"text\"}. "
            "Use it after opening a page or starting something slow, instead of polling observe. timeout is seconds (default 8, max 30)."
        ),
        "parameters": {"type": "object", "properties": {
            "expect": {"type": "object"}, "timeout": {"type": "number"}, "app": {"type": "string"}}, "required": ["expect"]},
    },
}

TABS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tabs",
        "description": (
            "Lists, switches to, or closes the user's browser tabs by part of a title or URL (Chrome family and Safari). "
            "list shows every tab in every window. switch brings the first match to the front. close closes every matching tab "
            "(match is required for switch/close). Use it when an extension or link opened a stray tab in front of the page you need."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "switch", "close"],
                       "description": "Defaults to list when omitted."},
            "match": {"type": "string", "description": "Part of a tab title or URL."},
            "app": {"type": "string", "description": "Defaults to Google Chrome."}}},
    },
}


OBSERVE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "observe",
        "description": (
            "visual=true also screenshots the window and reports what it looks like — use it to tell apart rows "
            "whose labels differ by a digit, to check a control is really enabled, or before an irreversible click. "
            "Then: the app, page title and URL, what has keyboard focus, any overlay in the way (an extension side "
            "panel, a popover), and up to 40 numbered elements (id, role, label, and the current value of text "
            "fields). Page content comes first. Ids stay valid for a few observations; a stale one is rejected, never "
            "mis-clicked. Call it before acting and whenever you are unsure what the screen shows. query keeps only "
            "elements whose label contains it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "App name, e.g. 'Google Chrome'; omit for the front app."},
                "query": {"type": "string", "description": "Only elements whose label contains this text."},
                "visual": {
                    "type": "boolean",
                    "description": (
                        "Also look at the screenshot and report the labels you can actually read. Slower "
                        "(~1.5s); worth it when the text reading is ambiguous."
                    ),
                },
                "visual_question": {
                    "type": "string",
                    "description": (
                        "What to ask the screenshot instead of the default 'describe and list labels', e.g. "
                        "'which row is highlighted?' or 'is the Save button greyed out?'."
                    ),
                },
            },
        },
    },
}

ACT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "act",
        "description": (
            "click / type (submit=true presses Return) / key (text is a shortcut like 'cmd+return', 'esc') / scroll (text "
            "'up' or 'down') one thing by element id from observe (copied exactly, e.g. 'e7.3') or by its label, then measures "
            "what changed. Returns {ok, effect:{focus, title, appeared, gone}} or {ok:false, error:{code, hint, candidates}}; "
            "a click that changed nothing carries warning 'no_effect'. Extension side panels are closed for you. Pass expect to "
            "wait for a result, e.g. {\"appears\": \"Message sent\"}, {\"title_has\": \"...\"}, {\"gone\": \"...\"}, "
            "{\"focus_role\": \"TextField\"} or {\"value_contains\": \"...\"}. steps=[...] runs a batch in one call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Element id from observe, or its label."},
                "action": {"type": "string", "enum": ["click", "type", "key", "scroll"]},
                "text": {"type": "string", "description": "Text to type, shortcut to press, or scroll direction."},
                "submit": {"type": "boolean", "description": "With action 'type': press Return after typing."},
                "replace": {"type": "boolean", "description": "With action 'type': select the field's current text first, so the typed text replaces it. Use when correcting a field."},
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


FIND_ON_PAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_on_page",
        "description": (
            "Scrolls until this text is on screen; a not-found is only reported after reaching the bottom, not after "
            "one look. The accessibility tree holds only the viewport, so a card that observe() does not list "
            "can still exist a scroll away. Returns {found, label, id, scrolls}. When it is not on the page it "
            "also returns any period/category filters that could be hiding it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Part of the label to look for, e.g. '2025' or 'Vol 03 No 44'."},
                "max_scrolls": {"type": "integer", "description": "Pages to scroll before giving up (default 12, max 30)."},
                "app": {"type": "string", "description": "App name; omit for the front app."},
            },
            "required": ["text"],
        },
    },
}
