"""Sqlite3 connections that always close on `with` exit.

The default `with sqlite3.connect(...) as conn:` commits/rolls back but relies
on refcount to close the connection. If an exception inside the block leaves a
retained traceback (logging, re-raise, exception chaining), the frame pins the
connection and its FD leaks. Over a long-running daemon that exhausts FDs and
surfaces as `sqlite3.OperationalError: unable to open database file` on later
connects. This factory closes in `__exit__` regardless, so the leak is gone.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

log = logging.getLogger("dbclose")

# Writes we replicate. SELECT/PRAGMA/DDL are never mirrored, and executescript()
# bypasses Connection.execute entirely, so schema creation stays local-only.
_WRITE_RE = re.compile(r"^\s*(?:INSERT|REPLACE|UPDATE|DELETE)\b", re.I)
_TABLE_RE = re.compile(
    r"^\s*(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)"
    r"\s+[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"`\]]?",
    re.I,
)


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


class MirroredConnection(ClosingConnection):
    """A connection that replicates its writes to the Turso replica on commit().

    Why at the connection layer instead of per store function: science scout
    wires each write by hand (`_mirror(sql, params)` next to the local execute),
    which is explicit but repetitive. For a store with a dozen write paths, that
    is a dozen chances to forget one. Intercepting here means every INSERT /
    UPDATE / DELETE the store already performs is mirrored, with no call-site
    changes.

    Semantics that matter:
      * **Committed only.** Writes are buffered and flushed after the local
        `commit()` succeeds, so a rolled-back transaction is never replicated.
        Buffering also lets one commit become one HTTP round trip.
      * **Best-effort.** A Turso outage logs a warning and drops the batch; the
        local commit already happened and stays authoritative.
      * **Table rewriting.** `<local table>` becomes `<namespace><local table>`
        so the replica never collides with another store's `meta` /
        `query_usage` / `subagent_prompts`. The prefix matches what
        scripts/turso_export_all.py created, so snapshot and live writes land
        in the same table.
      * **DDL excluded.** `executescript()` (schema creation) does not go through
        `execute()`, and CREATE/PRAGMA/SELECT never match the write regex.

    Only `Connection.execute` / `executemany` are intercepted. A `cursor()`-based
    caller would bypass mirroring — check before pointing this at a new store.
    """

    def __init__(self, *args: Any, namespace: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._turso_namespace = namespace
        self._turso_pending: list[tuple[str, tuple]] = []

    # ── envelope ─────────────────────────────────────────────────────────
    def execute(self, sql: str, params: Any = ()):  # type: ignore[override]
        cursor = super().execute(sql, params)
        self._queue(sql, params)
        return cursor

    def executemany(self, sql: str, seq_of_params: Any):  # type: ignore[override]
        rows = list(seq_of_params)
        cursor = super().executemany(sql, rows)
        for params in rows:
            self._queue(sql, params)
        return cursor

    def commit(self) -> None:
        super().commit()
        self._flush()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any):
        # CPython's Connection.__exit__ commits through the C API, so it does
        # NOT go through the commit() override above (verified: a store using
        # `with _conn() as conn:` never flushed until this hook existed).
        committed = False
        try:
            result = super().__exit__(exc_type, exc, tb)
            committed = exc_type is None
            return result
        finally:
            if committed:
                self._flush()
            else:
                self._turso_pending.clear()

    def rollback(self) -> None:
        super().rollback()
        self._turso_pending.clear()

    # ── internals ────────────────────────────────────────────────────────
    def _queue(self, sql: str, params: Any) -> None:
        if not self._turso_namespace or not _WRITE_RE.match(sql):
            return
        match = _TABLE_RE.match(sql)
        if not match:
            return
        table = match.group(1)
        mirrored = f"{sql[:match.start(1)]}{self._turso_namespace}{table}{sql[match.end(1):]}"
        try:
            bound = tuple(params) if params else ()
        except TypeError:  # mapping-style params
            bound = tuple(params.items()) if hasattr(params, "items") else ()
        self._turso_pending.append((mirrored, bound))

    def _flush(self) -> None:
        pending, self._turso_pending = self._turso_pending, []
        if not pending:
            return
        try:
            from store import turso_client

            turso_client.mirror_batch(pending)
        except Exception as exc:  # noqa: BLE001 — never break the local caller
            log.warning("turso mirror batch skipped (%d stmts): %s", len(pending), exc)


def connect_mirrored(path: str, namespace: str, **kwargs: Any) -> sqlite3.Connection:
    """connect() whose writes also replicate into `<namespace><table>` on Turso."""
    kwargs.setdefault(
        "factory",
        lambda *args, **kw: MirroredConnection(*args, namespace=namespace, **kw),
    )
    return connect(path, **kwargs)

