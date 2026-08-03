"""
Shared per-run observability trace — tool name/args/latency/error per step,
keyed by run_id. Originally local to local_agent_graph.py's LangGraph loop;
extracted so the flat cloud_client/ollama_client tool loop and specialists can
log into the same place too. One request, regardless of which path served it
(LangGraph agent, flat chat loop, or a specialist), now produces one
inspectable trace instead of only the LangGraph path having one.

Persisted to sqlite (not an in-memory dict) so traces survive a server
restart — a prior in-memory-only version lost every trace on reload.
"""
import json
import sqlite3
from store import dbclose
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "data" / "trace_store.sqlite"
_TRACE_MAX_RUNS = 200


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = dbclose.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS trace_entries ("
        "run_id TEXT NOT NULL, seq INTEGER NOT NULL, entry TEXT NOT NULL, "
        "PRIMARY KEY (run_id, seq))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS trace_runs ("
        "run_id TEXT PRIMARY KEY, first_seen INTEGER NOT NULL)"
    )
    return conn


def get_trace(run_id: str) -> list[dict] | None:
    """Return the trace for a run, or None if not found."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT entry FROM trace_entries WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
    if not rows:
        return None
    return [json.loads(r[0]) for r in rows]


def _touch_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO trace_runs (run_id, first_seen) "
        "VALUES (?, (SELECT COALESCE(MAX(first_seen), 0) + 1 FROM trace_runs))",
        (run_id,),
    )
    _evict_if_over_cap(conn)


def _evict_if_over_cap(conn: sqlite3.Connection) -> None:
    (count,) = conn.execute("SELECT COUNT(*) FROM trace_runs").fetchone()
    if count <= _TRACE_MAX_RUNS:
        return
    stale = conn.execute(
        "SELECT run_id FROM trace_runs ORDER BY first_seen LIMIT ?",
        (count - _TRACE_MAX_RUNS,),
    ).fetchall()
    stale_ids = [r[0] for r in stale]
    conn.executemany("DELETE FROM trace_runs WHERE run_id = ?", [(i,) for i in stale_ids])
    conn.executemany("DELETE FROM trace_entries WHERE run_id = ?", [(i,) for i in stale_ids])


def record(run_id: str, entry: dict) -> None:
    """Append one trace entry (tool name/args/latency/error) for this run."""
    with _conn() as conn:
        _touch_run(conn, run_id)
        # Atomic seq assignment: computing MAX(seq)+1 in Python then inserting
        # was a read-modify-write race — two concurrent callers (worker threads
        # + chat_ui) read the same MAX and collided on the PRIMARY KEY, dropping
        # one trace. A single INSERT...SELECT serializes with the table lock.
        conn.execute(
            "INSERT INTO trace_entries (run_id, seq, entry) "
            "SELECT ?, COALESCE(MAX(seq), -1) + 1, ? FROM trace_entries WHERE run_id = ?",
            (run_id, json.dumps(entry), run_id),
        )


def store_trace(run_id: str, trace: list[dict]) -> None:
    """Replace the whole trace list for a run (LangGraph's existing bulk-store pattern —
    it accumulates the list itself via LangGraph state and stores it once at the end)."""
    with _conn() as conn:
        _touch_run(conn, run_id)
        conn.execute("DELETE FROM trace_entries WHERE run_id = ?", (run_id,))
        conn.executemany(
            "INSERT INTO trace_entries (run_id, seq, entry) VALUES (?, ?, ?)",
            [(run_id, i, json.dumps(e)) for i, e in enumerate(trace)],
        )


def summarize_run(run_id: str) -> dict:
    """Single source of truth for ok/degraded status — used by both the
    orchestrator's [subagent ...] footer and the live trace dashboard, so the
    two never drift on what "degraded" means."""
    trace = get_trace(run_id) or []
    tools_called = [t.get("tool") for t in trace if t.get("tool")]
    errors = sum(1 for t in trace if t.get("error"))
    ok = bool(tools_called) and errors < len(tools_called)
    return {
        "run_id": run_id,
        "status": "ok" if ok else "degraded",
        "tools": tools_called,
        "errors": errors,
    }


def query_traces(
    run_id: str | None = None,
    tool: str | None = None,
    has_error: bool | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query traces with filters. Returns a list of runs, each being a dict with:
    {
        "run_id": str,
        "entries": list[dict]
    }
    """
    query = (
        "SELECT DISTINCT e.run_id FROM trace_entries e "
        "JOIN trace_runs r ON e.run_id = r.run_id "
        "WHERE 1=1"
    )
    params = []
    if run_id:
        query += " AND e.run_id = ?"
        params.append(run_id)
    if tool:
        query += " AND json_extract(e.entry, '$.tool') = ?"
        params.append(tool)
    if has_error is not None:
        if has_error:
            # Handle standard true values in SQLite/JSON (1 or true string/bool)
            query += (
                " AND (json_extract(e.entry, '$.error') = 1 "
                "OR json_extract(e.entry, '$.error') = 'true' "
                "OR json_extract(e.entry, '$.error') IS NOT NULL "
                "AND json_extract(e.entry, '$.error') != 0 "
                "AND json_extract(e.entry, '$.error') != 'false')"
            )
        else:
            query += (
                " AND (json_extract(e.entry, '$.error') = 0 "
                "OR json_extract(e.entry, '$.error') = 'false' "
                "OR json_extract(e.entry, '$.error') IS NULL)"
            )

    query += " ORDER BY r.first_seen DESC LIMIT ?"
    params.append(limit)

    with _conn() as conn:
        run_ids = [row[0] for row in conn.execute(query, params).fetchall()]
        results = []
        for rid in run_ids:
            trace = get_trace(rid)
            if trace:
                results.append({"run_id": rid, "entries": trace})
        return results
