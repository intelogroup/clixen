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

Pending approvals are durable SQLite records, single-use, and expire after the
TTL. The default database is inside Clixen's private app-data vault; tests and
deployments may override it with CLIXEN_CONFIRMATION_DB.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from tools.vault_paths import data_dir

_lock = threading.Lock()
_TTL_SECONDS = 3600  # stale, never-approved entries expire after an hour
_DB_PATH = Path(os.environ.get(
    "CLIXEN_CONFIRMATION_DB",
    str(data_dir() / "confirmations.db"),
))


def _conn():
    from store import dbclose
    conn = dbclose.connect(str(_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pending_confirmations ("
        "token TEXT PRIMARY KEY, tool_name TEXT NOT NULL, arguments TEXT NOT NULL, "
        "command TEXT NOT NULL, created_at REAL NOT NULL)"
    )
    return conn


def _expire_stale(conn) -> None:
    conn.execute(
        "DELETE FROM pending_confirmations WHERE created_at < ?",
        (time.time() - _TTL_SECONDS,),
    )


def request_confirmation(tool_name: str, arguments: dict, command: str) -> str:
    """Register a pending confirmation and return its token. Does not block."""
    token = uuid.uuid4().hex[:12]
    with _lock:
        with _conn() as conn:
            _expire_stale(conn)
            conn.execute(
                "INSERT INTO pending_confirmations(token, tool_name, arguments, command, created_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (token, tool_name, json.dumps(arguments or {}, ensure_ascii=False), command, time.time()),
            )
    return token


def pop_pending(token: str) -> dict | None:
    """Remove and return the pending entry, or None if unknown/already-resolved."""
    with _lock:
        with _conn() as conn:
            _expire_stale(conn)
            row = conn.execute(
                "SELECT tool_name, arguments, command, created_at FROM pending_confirmations WHERE token = ?",
                (token,),
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM pending_confirmations WHERE token = ?", (token,))
        try:
            arguments = json.loads(row[1])
        except (TypeError, ValueError):
            arguments = {}
        return {
            "token": token,
            "tool_name": row[0],
            "arguments": arguments,
            "command": row[2],
            "created_at": row[3],
        }


def list_pending() -> list[dict]:
    with _lock:
        with _conn() as conn:
            _expire_stale(conn)
            rows = conn.execute(
                "SELECT token, tool_name, command, created_at FROM pending_confirmations "
                "ORDER BY created_at"
            ).fetchall()
        return [
            {"token": row[0], "tool_name": row[1], "command": row[2],
             "waiting_seconds": round(time.time() - row[3], 1)}
            for row in rows
        ]
