"""Sqlite3 connections that always close on `with` exit.

The default `with sqlite3.connect(...) as conn:` commits/rolls back but relies
on refcount to close the connection. If an exception inside the block leaves a
retained traceback (logging, re-raise, exception chaining), the frame pins the
connection and its FD leaks. Over a long-running daemon that exhausts FDs and
surfaces as `sqlite3.OperationalError: unable to open database file` on later
connects. This factory closes in `__exit__` regardless, so the leak is gone.
"""
from __future__ import annotations

import sqlite3
from typing import Any


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect(path: str, **kwargs: Any) -> sqlite3.Connection:
    kwargs.setdefault("factory", ClosingConnection)
    conn = sqlite3.connect(path, **kwargs)
    # 2026-08-03: multi-process stores (event_log/trace_store/upload_store are
    # written by the worker process while chat_ui/crash_watchdog read them) had
    # no WAL and no busy_timeout — a concurrent writer raised "database is
    # locked" after the 5s default and the worker's broad except swallowed the
    # whole cycle. WAL allows concurrent readers + a single writer; a 10s
    # busy_timeout absorbs brief write contention.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
    except sqlite3.Error:
        pass  # read-only mounts / attached DBs — best-effort pragma
    return conn
