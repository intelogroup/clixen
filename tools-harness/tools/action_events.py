"""Structured tool events, so the orb can animate the *action* not the narration.

The bunny used to react to a string: the model's token stream contains
"⚙️ Running observe…" and the renderer scraped it. That meant the animation was a
side effect of whatever the narration happened to say, and it could not know whether
a call succeeded or how long it took.

Here the tool layer publishes two facts per call — started, then done with an
outcome — and the voice session forwards them as events. Sinks are per-process and
optional: the orchestrator, Telegram and evals simply have no sink attached, so
publishing is a no-op there.

Never let telemetry break a tool call: every publish is wrapped.
"""

from __future__ import annotations

import threading

_SINKS: list = []
_LOCK = threading.Lock()


def add_sink(fn) -> None:
    """Register a callable that receives {phase, tool, ok, ms, error}."""
    with _LOCK:
        if fn not in _SINKS:
            _SINKS.append(fn)


def remove_sink(fn) -> None:
    with _LOCK:
        if fn in _SINKS:
            _SINKS.remove(fn)


def clear() -> None:
    with _LOCK:
        _SINKS.clear()


def publish(phase: str, tool: str, *, ok: bool = True, ms: int = 0, error: str = "") -> None:
    with _LOCK:
        sinks = list(_SINKS)
    if not sinks:
        return
    payload = {"phase": phase, "tool": tool, "ok": ok, "ms": ms, "error": error}
    for fn in sinks:
        try:
            fn(payload)
        except Exception:
            # A broken sink must never surface as a tool failure.
            pass
