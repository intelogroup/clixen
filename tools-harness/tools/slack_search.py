"""Slack archive search via slacrawl's local SQLite FTS5 index.

Reads ~/.slacrawl/slacrawl.db (populated by `slacrawl sync` — requires the user
to add a Slack token to ~/.slacrawl/config.toml first).

We query SQLite directly rather than shelling out to `slacrawl search` so we
control formatting and error handling.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".slacrawl" / "slacrawl.db"

_NOT_SYNCED_HINT = (
    "[no slack data] DB has no messages yet. "
    "Add a Slack user token to ~/.slacrawl/config.toml, then run "
    "`slacrawl sync` (one-shot crawl). "
    "Get a token at https://api.slack.com/apps → your app → OAuth & Permissions."
)


SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "slack_search",
        "description": (
            "Search the user's local Slack archive using full-text search. "
            "Use when the user asks about something they saw or said in Slack — "
            "decisions, names, links, code snippets, what-was-said-about-X. "
            "Returns the most relevant message snippets with channel, sender, and timestamp. "
            "Read-only and offline; the archive is built locally by slacrawl."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "FTS5 query. Plain words are AND'd. Use quotes for phrases: "
                        "'launch date'. Use OR explicitly: 'redis OR cache'. "
                        "Prefix wildcard with *: 'kuber*'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max hits to return (1–25). Default 10.",
                    "default": 10,
                },
                "channel": {
                    "type": "string",
                    "description": "Optional channel name filter (e.g. 'eng-platform'). Omit to search all channels.",
                },
            },
            "required": ["query"],
        },
    },
}


STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "slack_status",
        "description": (
            "Report Slack archive coverage: how many messages/channels/users are "
            "synced, when the last sync ran, and whether the archive is ready to query. "
            "Use before slack_search if the answer 'no results' might mean 'no data'."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _open() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _ts_to_human(ts: str) -> str:
    try:
        seconds = float(ts)
    except (TypeError, ValueError):
        return ts or "?"
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _is_empty(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT count(*) AS n FROM messages").fetchone()
    return (row["n"] if row else 0) == 0


def search(query: str, limit: int = 10, channel: str = "") -> str:
    conn = _open()
    if conn is None:
        return _NOT_SYNCED_HINT
    try:
        if _is_empty(conn):
            return _NOT_SYNCED_HINT

        limit = max(1, min(int(limit or 10), 25))
        clean = (query or "").strip()
        if not clean:
            return "[error] empty query."

        sql = """
            SELECT
                m.channel_id,
                m.ts,
                m.user_id,
                m.text,
                c.name AS channel_name,
                COALESCE(u.real_name, u.display_name, u.name, m.user_id) AS sender,
                snippet(message_fts, 1, '«', '»', '…', 12) AS hit
            FROM message_fts
            JOIN messages m ON m.channel_id || '|' || m.ts = message_fts.message_key
            LEFT JOIN channels c ON c.id = m.channel_id
            LEFT JOIN users u ON u.id = m.user_id
            WHERE message_fts MATCH ?
        """
        params: list = [clean]
        if channel:
            sql += " AND c.name = ?"
            params.append(channel)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            return f"[fts query error] {e}. Try simpler terms or quote phrases."

        if not rows:
            scope = f" in #{channel}" if channel else ""
            return f"No matches for '{clean}'{scope}."

        out = [f"{len(rows)} match{'es' if len(rows) != 1 else ''} for '{clean}':"]
        for r in rows:
            ch = r["channel_name"] or r["channel_id"]
            sender = r["sender"] or "(unknown)"
            when = _ts_to_human(r["ts"])
            hit = (r["hit"] or "").replace("\n", " ").strip()
            out.append(f"  #{ch} · {sender} · {when}\n    {hit}")
        return "\n".join(out)
    finally:
        conn.close()


def status() -> str:
    conn = _open()
    if conn is None:
        return _NOT_SYNCED_HINT
    try:
        msgs = conn.execute("SELECT count(*) AS n FROM messages").fetchone()["n"]
        chans = conn.execute("SELECT count(*) AS n FROM channels").fetchone()["n"]
        users = conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
        last_row = conn.execute("SELECT max(ts) AS last FROM messages").fetchone()
        last_ts = last_row["last"] if last_row else None

        if msgs == 0:
            return _NOT_SYNCED_HINT
        last_human = _ts_to_human(last_ts) if last_ts else "unknown"
        return (
            f"Slack archive ready: {msgs} messages across {chans} channels, "
            f"{users} users. Most recent message: {last_human}. "
            f"Re-sync with `slacrawl sync` (one-shot) or `slacrawl tail` (live)."
        )
    finally:
        conn.close()
