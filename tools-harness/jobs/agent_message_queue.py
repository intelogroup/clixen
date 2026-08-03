"""
SQLite-backed agent message bus. Each row is a message from one agent to another.

Two tables:
  agent_messages   — message queue with status tracking
  agent_inboxes    — per-agent cursor for polling

Messages are persistent. Status transitions: unread → read → processing → done/replied.
"""
import json
import sqlite3
import uuid
from store import dbclose
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "agent_messages.sqlite"

_CREATE = """
CREATE TABLE IF NOT EXISTS agent_messages (
    id          TEXT PRIMARY KEY,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    priority    INTEGER DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'unread',
    reply_to    TEXT,
    created_at  TEXT NOT NULL,
    processed_at TEXT,
    result      TEXT
);
CREATE INDEX IF NOT EXISTS idx_am_to_status ON agent_messages(to_agent, status);
CREATE INDEX IF NOT EXISTS idx_am_created ON agent_messages(created_at);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = dbclose.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(_CREATE)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def send_message(from_agent: str, to_agent: str, subject: str, body: str, priority: int = 0) -> str:
    msg_id = uuid.uuid4().hex
    now = _now()
    with _conn() as con:
        con.execute(
            "INSERT INTO agent_messages (id, from_agent, to_agent, subject, body, priority, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, from_agent, to_agent, subject, body, priority, now),
        )
    return msg_id


def poll_messages(to_agent: str, status: str = "unread", limit: int = 10) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM agent_messages WHERE to_agent = ? AND status = ? "
            "ORDER BY priority DESC, created_at LIMIT ?",
            (to_agent, status, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_read(msg_ids: list[str]) -> None:
    if not msg_ids:
        return
    now = _now()
    placeholders = ",".join("?" for _ in msg_ids)
    with _conn() as con:
        con.execute(
            f"UPDATE agent_messages SET status = 'read', processed_at = ? WHERE id IN ({placeholders})",
            (now, *msg_ids),
        )


def mark_done(msg_id: str, result: str = "") -> None:
    now = _now()
    with _conn() as con:
        con.execute(
            "UPDATE agent_messages SET status = 'done', processed_at = ?, result = ? WHERE id = ?",
            (now, result[:2000], msg_id),
        )


def reply(original_msg_id: str, from_agent: str, subject: str, body: str) -> str:
    original = get_message(original_msg_id)
    if not original:
        raise ValueError(f"message {original_msg_id} not found")
    msg_id = send_message(
        from_agent=from_agent,
        to_agent=original["from_agent"],
        subject=subject,
        body=body,
    )
    with _conn() as con:
        con.execute(
            "UPDATE agent_messages SET reply_to = ? WHERE id = ?",
            (original_msg_id, msg_id),
        )
        con.execute(
            "UPDATE agent_messages SET status = 'replied' WHERE id = ?",
            (original_msg_id,),
        )
    return msg_id


def get_message(msg_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM agent_messages WHERE id = ?", (msg_id,)
        ).fetchone()
    return dict(row) if row else None


def pending_count(to_agent: str) -> int:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) as cnt FROM agent_messages WHERE to_agent = ? AND status = 'unread'",
            (to_agent,),
        ).fetchone()
    return row["cnt"] if row else 0


def list_conversation(msg_id: str, max_messages: int = 20) -> list[dict]:
    msgs = []
    current = get_message(msg_id)
    while current and len(msgs) < max_messages:
        msgs.append(current)
        if current["reply_to"]:
            current = get_message(current["reply_to"])
        else:
            break
    msgs.reverse()
    return msgs


def format_inbox(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        priority_tag = "!" * m["priority"] if m["priority"] else ""
        lines.append(
            f"  [{m['id'][:8]}] {priority_tag}{m['from_agent']} → {m['to_agent']}: "
            f"{m['subject']} ({m['body'][:120]})"
        )
    return "\n".join(lines)


def recent_messages(limit: int = 10) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM agent_messages ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
