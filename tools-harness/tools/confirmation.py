"""Human-in-the-loop confirmation gate for medium-risk shell commands.

Not a full sandbox — bash_exec still runs on the host with real permissions
(see registry.py's denylist for the hard-block tier). This adds a second tier:
commands that are legitimate but shouldn't fire unsupervised (git commit/push/
etc.) get parked here instead of running immediately; a human approves/denies
via POST /chat/local-agent/confirm, which drives registry.execute_confirmed()
to actually run (or discard) the call.

Async/poll design, not blocking: request_confirmation() stores the pending call
and returns immediately — the tool-loop thread that requested it is free right
away. (2026-07-28: previously blocked on a threading.Event inside the tool-call
thread, so a core.py restart while a confirmation was pending killed the whole
in-flight request, not just the confirmation state. Now the request completes
with an "[awaiting confirmation]" placeholder and the actual execution happens
later, driven entirely by the /confirm endpoint — a restart just means the
pending entry is gone and a stale approval 404s instead of a hung thread.)

ponytail: in-memory only, still doesn't survive a restart itself — an approval
sent for a *token* minted before a restart 404s. Add a durable (sqlite/file)
queue if confirmations need to survive that specific race; not built here
since restarts happen far less often than a normal single approval round-trip.
"""

from __future__ import annotations

import threading
import time
import uuid

_pending: dict[str, dict] = {}
_lock = threading.Lock()
_TTL_SECONDS = 3600  # stale, never-approved entries expire after an hour


def request_confirmation(tool_name: str, arguments: dict, command: str) -> str:
    """Register a pending confirmation and return its token. Does not block."""
    token = uuid.uuid4().hex[:12]
    with _lock:
        _expire_stale()
        _pending[token] = {
            "token": token,
            "tool_name": tool_name,
            "arguments": arguments,
            "command": command,
            "created_at": time.time(),
        }
    return token


def pop_pending(token: str) -> dict | None:
    """Remove and return the pending entry, or None if unknown/already-resolved."""
    with _lock:
        return _pending.pop(token, None)


def list_pending() -> list[dict]:
    with _lock:
        _expire_stale()
        return [
            {"token": e["token"], "tool_name": e["tool_name"], "command": e["command"],
             "waiting_seconds": round(time.time() - e["created_at"], 1)}
            for e in _pending.values()
        ]


def _expire_stale() -> None:
    """Caller must hold _lock."""
    now = time.time()
    for token in [t for t, e in _pending.items() if now - e["created_at"] > _TTL_SECONDS]:
        _pending.pop(token, None)
