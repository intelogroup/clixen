"""Bridge between the local core and the dev Chrome extension.

The extension is the only way to see console/DOM on the tab the user is *watching*
(ADR 0006). It runs with `activeTab` + `scripting` and no host permissions: it can
only touch a tab after the user acts, and only the tab they are looking at.

Transport is deliberately the dumbest thing that works: the extension polls the
core over localhost for one command at a time and posts the result back. No native
messaging host to install, no inbound port, no long-lived socket to babysit. The
core never reaches into the browser — it queues a request and waits.

If the extension is not loaded, `ask()` returns a clear "not connected" instead of
hanging, which is the difference between "audit unavailable" and "audit hung".
"""

from __future__ import annotations

import itertools
import threading
import time

_QUEUE: list[dict] = []
_LOG: list[str] = []
_TAKEN: set[str] = set()
_RESULTS: dict[str, dict] = {}
_LOCK = threading.Lock()
_IDS = itertools.count(1)
_LAST_SEEN = 0.0
_EVER_SEEN = False
DEFAULT_WAIT_S = 8.0
# Chrome suspends an MV3 service worker after ~30 s idle, which silently stops the
# poll loop. The extension re-arms a 30 s alarm as its heartbeat, so a command issued
# while it is asleep is still served - on the next tick. Hence this ceiling when we
# know the extension has been here before (measured 2026-10-25: answered at load,
# timed out a minute later, before the heartbeat existed).
WAKE_WAIT_S = 34.0
CONNECTED_WINDOW_S = 30.0


def _now() -> float:
    return time.time()


def log_event(msg: str) -> None:
    """The extension reports its own failures here (see /ext/log)."""
    with _LOCK:
        _LOG.append(f"{time.strftime('%H:%M:%S')} {msg[:300]}")
        del _LOG[:-20]


def recent_log() -> list[str]:
    with _LOCK:
        return list(_LOG)


def connected() -> bool:
    """Has the extension polled recently? (It polls every second while open.)"""
    return (_now() - _LAST_SEEN) < CONNECTED_WINDOW_S


def last_seen() -> float:
    return _LAST_SEEN


def enqueue(action: str, args: dict | None = None) -> str:
    with _LOCK:
        cmd_id = f"c{next(_IDS)}"
        _QUEUE.append({"id": cmd_id, "action": action, "args": args or {}, "queued_at": _now()})
        return cmd_id


def take() -> dict | None:
    """Hand the extension one queued command (called by /ext/poll)."""
    global _LAST_SEEN
    with _LOCK:
        _LAST_SEEN = _now()
        if not _QUEUE:
            return None
        cmd = _QUEUE.pop(0)
        _TAKEN.add(cmd["id"])
        return cmd


def was_taken(cmd_id: str) -> bool:
    """Did the extension pick this command up? If so, silence means it was suspended
    mid-flight - worth one retry, not a "broken tool"."""
    with _LOCK:
        return cmd_id in _TAKEN


def put_result(cmd_id: str, ok: bool, data=None, error: str = "") -> bool:
    global _LAST_SEEN
    with _LOCK:
        _LAST_SEEN = _now()
        if cmd_id not in _RESULTS and not any(c["id"] == cmd_id for c in _QUEUE):
            # Late result for a command the caller already gave up on: keep it
            # anyway, so a slow page does not look like a failed one next time.
            pass
        _RESULTS[cmd_id] = {"ok": ok, "data": data, "error": error, "at": _now()}
        return True


def wait_for(cmd_id: str, timeout: float = DEFAULT_WAIT_S) -> dict | None:
    deadline = _now() + max(0.5, timeout)
    while _now() < deadline:
        with _LOCK:
            found = _RESULTS.pop(cmd_id, None)
        if found:
            return found
        time.sleep(0.05)
    return None


def ask(action: str, args: dict | None = None, timeout: float = DEFAULT_WAIT_S) -> dict:
    """Queue a command and wait. Never raises; the caller gets a reason instead."""
    if not connected():
        if not _EVER_SEEN:
            return {
                "ok": False,
                "error": "not connected: the Clixen extension has not polled the core. "
                         "Load it (chrome://extensions > Load unpacked > extension/) and try again.",
            }
        # It has been here before, so Chrome has suspended the worker. Queue anyway
        # and wait out the heartbeat: failing a call the extension can still serve
        # would look like a broken tool when it is only asleep.
        timeout = max(timeout, WAKE_WAIT_S)
    # Two attempts. Chrome suspends the worker after ~30 s idle, and it can suspend
    # *between* taking a command and posting the result - which looked like "no answer
    # within 8s" for a request that was in fact picked up (measured 2026-10-25).
    for attempt in (1, 2):
        cmd_id = enqueue(action, args)
        wait = timeout if attempt == 1 else max(timeout, WAKE_WAIT_S)
        result = wait_for(cmd_id, wait)
        if result is not None:
            return result
        if not was_taken(cmd_id):
            break          # nobody picked it up: asleep or absent, a retry will not help
    if not _EVER_SEEN:
        return {
            "ok": False,
            "error": "not connected: the Clixen extension has not polled the core. "
                     "Load it (chrome://extensions > Load unpacked > extension/) and try again.",
        }
    return {"ok": False, "error": f"no answer from the extension within {timeout:.0f}s"}


def reset_for_tests() -> None:
    global _LAST_SEEN, _EVER_SEEN
    with _LOCK:
        _QUEUE.clear()
        _RESULTS.clear()
        _TAKEN.clear()
        _LAST_SEEN = 0.0
        _EVER_SEEN = False


def mark_seen() -> None:
    """Tests and the poll endpoint use this to simulate a live extension."""
    global _LAST_SEEN, _EVER_SEEN
    with _LOCK:
        _LAST_SEEN = _now()
        _EVER_SEEN = True


EXT_PROBE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "page_probe",
        "description": (
            "kind='viewport' answers 'is there more page below the fold?' and lists the fields "
            "that are off screen — check it before deciding a field is missing or clicking "
            "blindly. kind='links' lists downloadable URLs on the tab (use instead of clicking "
            "a site's Download button, which opens Save As and saves nothing to the workspace). "
            "Also console output and page errors, DOM elements, visible text, or click a "
            "control by label, through the Clixen browser extension."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["console", "dom", "text", "links", "viewport", "click"],
                    "description": ("What to read: console, dom, text, links (downloadable URLs on this tab), or 'click' to press a control by label"),
                },
                "level": {"type": "string", "description": "console only: error, warning, log, info"},
                "limit": {"type": "integer", "description": "How many entries/elements (default 50/120)"},
                "text": {"type": "string", "description": "click only: visible label to press"},
                "timeout": {"type": "number", "description": "Seconds to wait for the extension (default 8)"},
            },
            "required": ["kind"],
        },
    },
}


def probe(kind: str = "dom", level: str = "", limit: int = 0, text: str = "", timeout: float = DEFAULT_WAIT_S) -> str:
    """Ask the extension for one page fact, formatted for a model to read."""
    args = {"kind": kind}
    if level:
        args["level"] = level
    if limit:
        args["limit"] = int(limit)
    if text:
        args["text"] = text

    res = ask("probe", args, timeout=timeout)
    if not res.get("ok"):
        return f"[error] {res.get('error', 'extension did not answer')}"

    data = res.get("data") or {}
    if kind == "viewport":
        lines = [
            f"Viewport: {data.get('innerHeight')}px tall, page {data.get('scrollHeight')}px, "
            f"scrolled {data.get('scrollY')}px. {data.get('below')}px more below, "
            f"{data.get('above')}px above. at_bottom={data.get('at_bottom')}.",
        ]
        for el in data.get("offscreen") or []:
            lines.append(f"- {el.get('where')}: {el.get('label')} (y={el.get('y')})")
        if data.get("offscreen"):
            lines.append("Those exist in the document but are not in view: scroll to them "
                         "(act action='scroll') before clicking or typing, or use find_on_page.")
        else:
            lines.append(data.get("hint") or "")
        return "\n".join(lines)

    if kind == "links":
        links = data.get("links") or []
        if not links:
            return ("No downloadable links on this tab "
                    f"(match={data.get('matching') or 'file types'}). If the file is served by an "
                    "API, use scrapling_api_capture; if the button is JavaScript, ask again "
                    "with match='' after the click, or read the API.")
        lines = [f"{len(links)} downloadable link(s) on this tab:"]
        for link in links:
            label = f" — {link['text']}" if link.get("text") else ""
            lines.append(f"- {link['href']}{label}")
        lines.append("Save one with workspace_download(url) — it reports the byte count. "
                     "Do not click the site's Download button: that opens a Save As dialog "
                     "and no file lands in the workspace.")
        return "\n".join(lines)

    if kind == "console":
        entries = data.get("entries") or []
        total = data.get("total")
        filtered = data.get("filtered_by")
        types = data.get("types") or {}
        if not entries:
            if total:
                # The filter hid everything: saying "nothing captured" would make the
                # model report a clean console that is not clean.
                present = ", ".join(f"{k}x{v}" for k, v in sorted(types.items()))
                return (
                    f"No {filtered} entries, but {total} message(s) are captured ({present}). "
                    "Call page_probe again without level, or with the right level, to read them."
                )
            return "No console output captured in this tab yet (the extension records from injection onward)."
        counts = data.get("count", len(entries))
        shown_total = total if total is not None else counts
        lines = [f"Console ({counts} shown, {data.get('errors', 0)} error-level of {shown_total} captured):"]
        lines += [f"- {e.get('type')}: {e.get('text')}" for e in entries[-40:]]
        return "\n".join(lines)
    if kind == "text":
        return (data.get("text") or "")[:8000] or "(page has no text)"
    if kind == "click":
        return f"Clicked '{data.get('clicked')}'" if data.get("ok") else f"[error] {data.get('error')}"
    elements = data.get("elements") or []
    head = f"{data.get('title', '')} — {data.get('url', '')}"
    consent = data.get("consent") or []
    lines = [head, f"{len(elements)} visible elements:"]
    lines += [f"- {e['tag']}{('[' + e['role'] + ']') if e['role'] else ''} {e['label']}" for e in elements[:60]]
    if consent:
        lines.append(f"consent controls: {', '.join(consent)}")
    return "\n".join(lines)
