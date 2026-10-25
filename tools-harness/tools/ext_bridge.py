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
_RESULTS: dict[str, dict] = {}
_LOCK = threading.Lock()
_IDS = itertools.count(1)
_LAST_SEEN = 0.0
DEFAULT_WAIT_S = 8.0
CONNECTED_WINDOW_S = 30.0


def _now() -> float:
    return time.time()


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
        return _QUEUE.pop(0) if _QUEUE else None


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
        return {
            "ok": False,
            "error": "not connected: the Clixen extension has not polled the core. "
                     "Load it (chrome://extensions > Load unpacked > extension/) and try again.",
        }
    cmd_id = enqueue(action, args)
    result = wait_for(cmd_id, timeout)
    if result is None:
        return {"ok": False, "error": f"no answer from the extension within {timeout:.0f}s"}
    return result


def reset_for_tests() -> None:
    global _LAST_SEEN
    with _LOCK:
        _QUEUE.clear()
        _RESULTS.clear()
        _LAST_SEEN = 0.0


def mark_seen() -> None:
    """Tests and the poll endpoint use this to simulate a live extension."""
    global _LAST_SEEN
    with _LOCK:
        _LAST_SEEN = _now()


EXT_PROBE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "page_probe",
        "description": (
            "Inspect the tab the user is looking at, through the Clixen browser extension: "
            "console output and page errors, DOM elements, visible text, or click a control by "
            "its visible label. Requires the extension loaded and connected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["console", "dom", "text", "click"],
                    "description": "What to read, or 'click' to press a control by label",
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
    if kind == "console":
        entries = data.get("entries") or []
        if not entries:
            return "No console output captured in this tab yet (the extension records from injection onward)."
        lines = [f"Console ({data.get('count', len(entries))} captured, {data.get('errors', 0)} error-level):"]
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
