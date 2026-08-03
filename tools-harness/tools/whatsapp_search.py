"""Search the local WhatsApp message archive built by whatsapp_bridge.js.

The bridge logs every incoming + outgoing message to ~/.clixen/whatsapp.db
(SQLite + FTS5). This module reads it the same way slack_search reads slacrawl's DB.

If logging hasn't been enabled (no DB or empty DB), we surface a clear hint.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".clixen" / "whatsapp.db"

_NO_DATA_HINT = (
    "[no whatsapp data] DB has no messages yet. "
    "Make sure whatsapp_bridge.js is running with logging enabled "
    "(it writes to ~/.clixen/whatsapp.db automatically). "
    "If the file doesn't exist, restart the bridge after pulling the latest code."
)


SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "whatsapp_search",
        "description": (
            "Search the user's local WhatsApp message archive (built by the bridge). "
            "Use when the user asks 'what did X say on whatsapp', 'find that link from Y', "
            "or wants to recall a past conversation. Read-only and offline."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "FTS5 query (or LIKE substring fallback). "
                        "Plain words AND'd. Use quotes for phrases."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max hits (1–25). Default 10.",
                    "default": 10,
                },
                "contact": {
                    "type": "string",
                    "description": "Optional: filter to a JID, phone number, or push-name substring.",
                },
                "days": {
                    "type": "integer",
                    "description": "Limit to messages from the last N days. Omit for all-time.",
                },
            },
            "required": ["query"],
        },
    },
}

STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "whatsapp_status",
        "description": (
            "Report WhatsApp archive coverage: total messages, contacts, date range, "
            "and whether the DB is being written by the bridge."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _open() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def _ts_to_human(ts: int | None) -> str:
    if ts is None:
        return "?"
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError):
        return "?"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _is_empty(conn: sqlite3.Connection) -> bool:
    try:
        n = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        return n == 0
    except sqlite3.OperationalError:
        return True


def search(query: str, limit: int = 10, contact: str = "", days: int = 0) -> str:
    conn = _open()
    if conn is None:
        return _NO_DATA_HINT
    try:
        if not _has_table(conn, "messages") or _is_empty(conn):
            return _NO_DATA_HINT
        clean = (query or "").strip()
        if not clean:
            return "[whatsapp error] empty query."
        cap = max(1, min(int(limit or 10), 25))

        use_fts = _has_table(conn, "message_fts")
        if use_fts:
            sql = """
                SELECT m.msg_id, m.jid, m.push_name, m.from_me, m.text, m.ts
                FROM message_fts f
                JOIN messages m ON m.id = f.rowid
                WHERE message_fts MATCH ?
            """
            params: list = [clean]
        else:
            sql = """
                SELECT msg_id, jid, push_name, from_me, text, ts
                FROM messages
                WHERE text LIKE ? COLLATE NOCASE
            """
            params = [f"%{clean}%"]

        if contact:
            cf = f"%{contact}%"
            sql += " AND (jid LIKE ? OR push_name LIKE ?)"
            params.extend([cf, cf])
        if days and days > 0:
            cutoff = int(datetime.now(tz=timezone.utc).timestamp()) - (days * 86400)
            sql += " AND ts >= ?"
            params.append(cutoff)

        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(cap)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            return f"[whatsapp fts error] {e}. Try simpler terms."

        if not rows:
            scope = f" with {contact}" if contact else ""
            window = f" in last {days}d" if days else ""
            return f"No WhatsApp matches for '{clean}'{scope}{window}."

        out = [f"{len(rows)} WhatsApp match{'es' if len(rows) != 1 else ''} for '{clean}':"]
        for r in rows:
            who = "me" if r["from_me"] else (r["push_name"] or r["jid"] or "?")
            when = _ts_to_human(r["ts"])
            text = (r["text"] or "").replace("\n", " ").strip()
            if len(text) > 240:
                text = text[:237] + "..."
            out.append(f"  {when} · {who}\n    {text}")
        return "\n".join(out)
    finally:
        conn.close()


def status() -> str:
    conn = _open()
    if conn is None:
        return _NO_DATA_HINT
    try:
        if not _has_table(conn, "messages"):
            return _NO_DATA_HINT
        n = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        if n == 0:
            return _NO_DATA_HINT
        contacts = conn.execute("SELECT count(DISTINCT jid) FROM messages").fetchone()[0]
        rng = conn.execute("SELECT min(ts), max(ts) FROM messages WHERE ts > 0").fetchone()
        first = _ts_to_human(rng[0]) if rng else "?"
        last = _ts_to_human(rng[1]) if rng else "?"
        return (
            f"WhatsApp archive: {n:,} messages across {contacts} contacts. "
            f"Range: {first} → {last}."
        )
    finally:
        conn.close()
