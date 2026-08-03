"""
Structured event log for background job/workflow execution — records a
{ts, kind, status, detail} row at each state transition, so crash_watchdog.py
can detect failures by reading structured status transitions instead of
regex-tailing text logs (which miss any crash shape the regex doesn't already
know about — confirmed live: whatsapp_bot.py's crash was invisible purely
because it had zero logging, unrelated to the regex itself).
"""
import json
import sqlite3
import time
from store import dbclose
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "data" / "event_log.sqlite"
_MAX_ROWS = 2000


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = dbclose.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
        "kind TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL)"
    )
    return conn


def record_event(kind: str, status: str, detail: dict) -> None:
    """Append one structured event. kind: 'job'/'workflow'. status: 'started'/'done'/'failed'."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (ts, kind, status, detail) VALUES (?, ?, ?, ?)",
            (time.time(), kind, status, json.dumps(detail)),
        )
        (count,) = conn.execute("SELECT COUNT(*) FROM events").fetchone()
        if count > _MAX_ROWS:
            conn.execute(
                "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id LIMIT ?)",
                (count - _MAX_ROWS,),
            )


def events_since(last_id: int) -> list[dict]:
    """New events with id > last_id, oldest first — mirrors crash_watchdog's log-offset polling."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, ts, kind, status, detail FROM events WHERE id > ? ORDER BY id",
            (last_id,),
        ).fetchall()
    return [
        {"id": r[0], "ts": r[1], "kind": r[2], "status": r[3], "detail": json.loads(r[4])}
        for r in rows
    ]


def latest_id() -> int:
    """Current max event id — used to start polling from 'now' instead of replaying history."""
    with _conn() as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
    return row[0]


def recent(hours: float = 3.0, limit: int = 8) -> list[dict]:
    """Most-recent-first events within the window, oldest dropped past limit."""
    cutoff = time.time() - hours * 3600
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, ts, kind, status, detail FROM events WHERE ts >= ? "
            "ORDER BY id DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [
        {"id": r[0], "ts": r[1], "kind": r[2], "status": r[3], "detail": json.loads(r[4])}
        for r in rows
    ]


def recent_activity_block(hours: float = 3.0, limit: int = 8) -> str:
    """Prompt-injection block, same shape/contract as memory_tools.recall_block():
    returns "" on nothing to show, else a small markdown section. Enriches
    workflow 'done' events with their last_result summary (event detail alone
    only has {workflow_id, automation_id} — no payload) by looking it up from
    workflow_store, since the point is showing what data automations saved,
    not just that they ran."""
    events = recent(hours=hours, limit=limit)
    if not events:
        return ""
    from store import workflow_store  # local import: avoid a store/store circular import at module load

    lines = []
    for e in events:
        d = e["detail"]
        when = time.strftime("%H:%M", time.localtime(e["ts"]))
        name = d.get("automation_id") or d.get("task_name") or "?"
        if e["kind"] == "workflow" and e["status"] == "done":
            inst = workflow_store.get_workflow_instance(d.get("workflow_id", ""))
            result = (inst or {}).get("last_result") or {}
            summary = result.get("message") or result.get("detail") or ""
            summary = (summary[:120] + "…") if len(summary) > 120 else summary
            lines.append(f"- [{when}] {name}: done" + (f" — {summary}" if summary else ""))
        elif e["status"] == "failed":
            lines.append(f"- [{when}] {name}: FAILED — {d.get('error', 'unknown error')[:120]}")
        # 'started' events are skipped — noise, superseded by the matching done/failed row
    if not lines:
        return ""
    return "## Recent background activity\n" + "\n".join(lines) + "\n"
