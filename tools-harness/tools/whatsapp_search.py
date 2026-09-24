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

RECENT_CHATS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "whatsapp_recent_chats",
        "description": (
            "List the user's most recent WhatsApp conversations from the local archive. "
            "Returns contact name, latest text, direction, and timestamp. Read-only and offline."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent chats (1–25). Default 10.",
                    "default": 10,
                },
                "contact": {
                    "type": "string",
                    "description": "Optional: filter to one contact by JID, phone number, or name substring.",
                },
            },
            "required": [],
        },
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
            fts_query = '"' + clean.replace('"', '""') + '"'
            sql = """
                SELECT m.msg_id, m.jid, m.push_name, m.from_me, m.text, m.ts
                FROM message_fts f
                JOIN messages m ON m.id = f.rowid
                WHERE message_fts MATCH ?
            """
            params: list = [fts_query]
        else:
            sql = """
                SELECT msg_id, jid, push_name, from_me, text, ts
                FROM messages
                WHERE text LIKE ? COLLATE NOCASE
            """
            params = [f"%{clean}%"]

        if contact:
            cf = f"%{contact}%"
            contact_col = "m.jid" if use_fts else "jid"
            sql += (
                f" AND ({contact_col} LIKE ? OR push_name LIKE ? OR {contact_col} IN ("
                "SELECT jid FROM contacts WHERE name LIKE ? OR notify LIKE ? OR verified_name LIKE ? "
                "UNION SELECT lid FROM contacts WHERE name LIKE ? OR notify LIKE ? OR verified_name LIKE ?"
                "))"
            )
            params.extend([cf, cf, cf, cf, cf, cf, cf, cf])
        if days and days > 0:
            cutoff = int(datetime.now(tz=timezone.utc).timestamp()) - (days * 86400)
            sql += " AND ts >= ?"
            params.append(cutoff)

        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(cap)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            if use_fts and "no such column" in str(e):
                like_sql = """
                    SELECT msg_id, jid, push_name, from_me, text, ts
                    FROM messages
                    WHERE text LIKE ? COLLATE NOCASE
                """
                like_params: list = [f"%{clean}%"]
                if contact:
                    like_sql += (
                        " AND (jid LIKE ? OR push_name LIKE ? OR jid IN ("
                        "SELECT jid FROM contacts WHERE name LIKE ? OR notify LIKE ? OR verified_name LIKE ? "
                        "UNION SELECT lid FROM contacts WHERE name LIKE ? OR notify LIKE ? OR verified_name LIKE ?"
                        "))"
                    )
                    like_params.extend([cf, cf, cf, cf, cf, cf, cf, cf])
                if days and days > 0:
                    like_params.append(cutoff)
                like_sql += " ORDER BY ts DESC LIMIT ?"
                like_params.append(cap)
                try:
                    rows = conn.execute(like_sql, like_params).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            else:
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


def _write_conn() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
        return conn
    except sqlite3.OperationalError:
        return None


def index_pending_media(limit: int = 5) -> int:
    """Transcribe/OCR/extract-text any downloaded WhatsApp media the bridge
    hasn't indexed yet (media_path set, media_indexed=0), then rewrite that
    row's `text` column with the extracted content so it's FTS-searchable
    like any other message. Called from jobs/worker.py's poll loop — small
    batch per call so a slow whisper/OCR run never blocks other jobs.

    ponytail: video messages are tagged but never transcribed (no audio-track
    extraction wired) — add an ffmpeg extract-audio + transcribe_audio step
    here if voice content inside videos needs to be searchable too.
    Returns the number of rows processed (attempted, not necessarily
    successful — a failed extraction still marks media_indexed=1 to avoid
    retrying forever on a broken file).
    """
    conn = _write_conn()
    if conn is None:
        return 0
    try:
        if not _has_table(conn, "messages"):
            return 0
        rows = conn.execute(
            "SELECT id, media_path, media_type, text FROM messages "
            "WHERE media_path IS NOT NULL AND media_indexed = 0 "
            "ORDER BY id LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        for row in rows:
            row_id, media_path, media_type, tag_text = row[0], row[1], row[2], row[3]
            new_text = tag_text
            try:
                if media_type == "audio":
                    # generic Whisper (base, then large-v3) both struggled on
                    # this archive's mostly-Haitian-Creole audio — base
                    # hallucinated fluent French nonsense, large-v3 leaned
                    # French with 0.26-0.59 language confidence (confirmed
                    # live 2026-09-22). Swapped to a Creole-specific Whisper
                    # fine-tune, cross-validated against large-v3's output on
                    # the same clips (same underlying content, cleaner Kreyòl
                    # orthography). Revisit if this archive's contacts ever
                    # skew non-Creole.
                    from tools.audio_tools import transcribe_haitian_creole
                    result = transcribe_haitian_creole(media_path)
                    transcript = (result.get("text") or "").strip()
                    if transcript:
                        new_text = f"[voice note] {transcript}"
                elif media_type in ("photo", "sticker"):
                    from tools.ocr import execute as ocr_execute
                    ocr_text = (ocr_execute(media_path) or "").strip()
                    if ocr_text:
                        new_text = f"[photo] {ocr_text}"
                elif media_type == "document" and str(media_path).lower().endswith(".pdf"):
                    from tools.structured import read_pdf
                    content = read_pdf(media_path, pages="1-10")
                    if content and not content.startswith("PDF read error"):
                        new_text = f"[document] {content[:4000]}"
                # video / unrecognized document types: leave the tag as-is,
                # just mark indexed so this row isn't retried every cycle.
            except Exception as e:
                new_text = f"{tag_text} [index error: {e}]"
            conn.execute(
                "UPDATE messages SET text = ?, media_indexed = 1 WHERE id = ?",
                (new_text, row_id),
            )
            conn.commit()
        return len(rows)
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


def recent_chats(limit: int = 10, contact: str = "") -> str:
    """Return one latest archived message per WhatsApp conversation.

    With `contact`, filters to conversations whose jid, lid, push_name, or
    saved contact name/notify/verified_name matches the substring.
    """
    conn = _open()
    if conn is None:
        return _NO_DATA_HINT
    try:
        if not _has_table(conn, "messages") or _is_empty(conn):
            return _NO_DATA_HINT
        cap = max(1, min(int(limit or 10), 25))
        contact_filter = ""
        params: list = [cap]
        if contact:
            cf = f"%{contact}%"
            contact_filter = """
                WHERE latest.contact_key IN (
                    SELECT jid FROM contacts WHERE jid LIKE ? OR lid LIKE ?
                        OR name LIKE ? OR notify LIKE ? OR verified_name LIKE ?
                    UNION SELECT lid FROM contacts WHERE jid LIKE ? OR lid LIKE ?
                        OR name LIKE ? OR notify LIKE ? OR verified_name LIKE ?
                )
                OR latest.contact_key LIKE ? OR latest.push_name LIKE ?
            """
            params = [cf] * 12 + [cap]
        rows = conn.execute(
            f"""
            WITH canon AS (
                -- A contact can appear under two jids (phone-number jid and a
                -- privacy @lid jid); collapse to one identity per contact so
                -- its messages aren't split across two "latest" slots.
                SELECT m.*,
                       COALESCE((SELECT c.jid FROM contacts c WHERE c.lid = m.jid), m.jid) AS contact_key
                FROM messages m
            ),
            latest AS (
                SELECT canon.*
                FROM canon
                JOIN (
                    SELECT contact_key, MAX(ts * 1000000 + id) AS max_key
                    FROM canon
                    GROUP BY contact_key
                ) x ON x.max_key = canon.ts * 1000000 + canon.id
                WHERE NOT EXISTS (
                    SELECT 1 FROM owner_jids o WHERE
                        CASE WHEN instr(o.jid, ':') > 0
                             THEN substr(o.jid, 1, instr(o.jid, ':') - 1) || substr(o.jid, instr(o.jid, '@'))
                             ELSE o.jid END
                        = CASE WHEN instr(canon.contact_key, ':') > 0
                               THEN substr(canon.contact_key, 1, instr(canon.contact_key, ':') - 1) || substr(canon.contact_key, instr(canon.contact_key, '@'))
                               ELSE canon.contact_key END
                )
            )
            SELECT latest.contact_key AS jid, latest.push_name, latest.from_me, latest.text, latest.ts,
                   c.name AS contact_name, c.notify AS contact_notify
            FROM latest
            LEFT JOIN contacts c
              ON c.jid = latest.contact_key OR c.lid = latest.contact_key
            {contact_filter}
            ORDER BY latest.ts DESC, latest.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        if not rows:
            scope = f" matching '{contact}'" if contact else ""
            return f"No archived WhatsApp conversations{scope}."

        out = [f"{len(rows)} recent WhatsApp chat{'s' if len(rows) != 1 else ''}:"]
        for row in rows:
            name = row["contact_name"] or row["contact_notify"] or row["push_name"] or row["jid"] or "?"
            direction = "me" if row["from_me"] else "them"
            text = (row["text"] or "").replace("\n", " ").strip()
            if len(text) > 240:
                text = text[:237] + "..."
            out.append(f"  {_ts_to_human(row['ts'])} · {name} · {direction}\n    {text}")
        return "\n".join(out)
    except sqlite3.OperationalError as e:
        return f"[whatsapp recent chats error] {e}"
    finally:
        conn.close()
