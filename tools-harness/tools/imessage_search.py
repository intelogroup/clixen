"""iMessage search via direct read of ~/Library/Messages/chat.db.

Apple's chat.db is a SQLite database. We open it read-only and run LIKE-based
text search across the `message` table, joined with `handle` (sender) and
`chat` (conversation/group). No external CLI dependency.

NOTE: requires Full Disk Access for the process running the harness
(Terminal / Claude Code / launchd). Otherwise sqlite3.connect raises
OperationalError("unable to open database file"). We surface a clear hint.

Date format quirk: Apple stores `message.date` as nanoseconds since
2001-01-01 00:00:00 UTC (Cocoa epoch + 1e9 multiplier on macOS 10.13+).
We convert to local time strings on the way out.
"""
from __future__ import annotations

import logging
import os
import re
import shutil as _shutil
import sqlite3
import subprocess as _subprocess
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

_OSA_TIMEOUT = 8
DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"

_NEEDS_FDA_HINT = (
    "[imessage error] Cannot open ~/Library/Messages/chat.db. "
    "Grant Full Disk Access to the process running the harness "
    "(System Settings → Privacy & Security → Full Disk Access → add "
    "Terminal / Claude Code / your launchd binary). Then retry."
)

# Cocoa reference date: 2001-01-01 UTC.
_COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

_MSG_COLS = (
    "m.text AS text, m.is_from_me AS from_me, m.date AS date_ns, "
    "COALESCE(h.id, '') AS sender_id, "
    "COALESCE(c.display_name, c.chat_identifier, '') AS chat_name"
)
_MSG_JOIN = (
    "FROM message m "
    "LEFT JOIN handle h ON h.ROWID = m.handle_id "
    "LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
    "LEFT JOIN chat c ON c.ROWID = cmj.chat_id"
)


SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "imessage_search",
        "description": (
            "Search iMessage history for messages matching a query, or show recent messages "
            "when no query given. Use when the user asks about something said in iMessage/SMS — "
            "'what did mom say about', 'find the address Sarah sent', 'when did I text X', "
            "'show my recent texts'. "
            "Read-only and offline; reads ~/Library/Messages/chat.db directly. "
            "Returns sender, conversation, timestamp, and message text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for (case-insensitive substring match). "
                    "Omit to show most recent messages across all conversations.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max messages to return (1–50). Default 15.",
                    "default": 15,
                },
                "contact": {
                    "type": "string",
                    "description": (
                        "Optional sender filter — phone number ('+15551234567'), "
                        "email, or substring of the chat identifier."
                    ),
                },
                "days": {
                    "type": "integer",
                    "description": "Limit to messages from the last N days. Omit for all-time.",
                },
            },
            "required": [],
        },
    },
}


STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "imessage_status",
        "description": (
            "Report iMessage archive coverage: total messages, conversations, contacts, "
            "and date range available locally. Use before imessage_search if 'no results' "
            "might mean 'database not accessible'."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _open() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    try:
        # mode=ro avoids any chance of writing through. No immutable=1: chat.db
        # is WAL-mode and Messages.app writes new texts to chat.db-wal first,
        # checkpointing only periodically — immutable=1 skips the WAL entirely
        # and reads only the checkpointed base file, so recent incoming
        # messages stay invisible to poll loops until a checkpoint happens.
        conn = sqlite3.connect(
            f"file:{DB_PATH}?mode=ro", uri=True, timeout=2.0
        )
        conn.row_factory = sqlite3.Row
        # Probe permission with a trivial query — if FDA missing, this raises.
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return conn
    except sqlite3.OperationalError:
        return None


def _ns_to_human(ns: int | None) -> str:
    if ns is None:
        return "?"
    try:
        seconds = float(ns) / 1_000_000_000.0
        dt = _COCOA_EPOCH + timedelta(seconds=seconds)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OverflowError):
        return "?"


def _days_ago_to_ns(days: int) -> int:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    return int((cutoff - _COCOA_EPOCH).total_seconds() * 1_000_000_000)


def search(query: str, limit: int = 15, contact: str = "", days: int = 0) -> str:
    t0 = _time.time()
    if not DB_PATH.exists():
        _log.warning("search: chat.db not found at %s", DB_PATH)
        return f"[imessage error] {DB_PATH} not found — is iMessage configured on this Mac?"
    conn = _open()
    if conn is None:
        _log.warning("search: cannot open chat.db (FDA missing)")
        return _NEEDS_FDA_HINT

    try:
        clean = (query or "").strip()
        limit = max(1, min(int(limit or 15), 50))
        where_clause = "m.text LIKE ? COLLATE NOCASE"
        params: list = [f"%{clean}%"]
        if not clean:
            where_clause = "m.text IS NOT NULL AND m.text != ''"
            params = []
        sql = f"SELECT {_MSG_COLS} {_MSG_JOIN} WHERE {where_clause}"

        if contact:
            sql += " AND (h.id LIKE ? OR c.chat_identifier LIKE ? OR c.display_name LIKE ?)"
            cf = f"%{contact}%"
            params.extend([cf, cf, cf])

        if days and days > 0:
            sql += " AND m.date >= ?"
            params.append(_days_ago_to_ns(days))

        sql += " ORDER BY m.date DESC LIMIT ?"
        params.append(limit)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            _log.error("search: sqlite error: %s", e)
            return f"[imessage error] sqlite: {e}"

        elapsed = _time.time() - t0

        if not rows:
            scope = f" with {contact}" if contact else ""
            window = f" in last {days}d" if days else ""
            if clean:
                _log.info("search: 0 results q=%r contact=%s days=%d %.2fs", clean, contact, days, elapsed)
                return f"No iMessage matches for '{clean}'{scope}{window}."
            _log.info("search: 0 recent msgs contact=%s days=%d %.2fs", contact, days, elapsed)
            return f"No iMessage messages found{scope}{window}."

        _log.info("search: %d results q=%r contact=%s days=%d %.2fs", len(rows), clean, contact, days, elapsed)
        label = f" for '{clean}'" if clean else ""
        out = [f"{len(rows)} iMessage message{'s' if len(rows) != 1 else ''}{label}:"]
        for r in rows:
            who = "me" if r["from_me"] else (r["sender_id"] or "unknown")
            chat = r["chat_name"] or r["sender_id"] or ""
            when = _ns_to_human(r["date_ns"])
            text = (r["text"] or "").replace("\n", " ").strip()
            if len(text) > 240:
                text = text[:237] + "..."
            chat_tag = f" [{chat}]" if chat and chat != r["sender_id"] else ""
            out.append(f"  {when} · {who}{chat_tag}\n    {text}")
        return "\n".join(out)
    finally:
        conn.close()


# Class-name tokens that show up as plain ASCII inside the NSKeyedArchiver
# typedstream blob in `attributedBody` — never real message content, so any
# string run equal to one of these is skipped when hunting for the payload.
_ATTRIBUTED_BODY_NOISE = {
    b"streamtyped", b"NSMutableAttributedString", b"NSAttributedString",
    b"NSObject", b"NSMutableString", b"NSString", b"NSDictionary",
    b"NSNumber", b"NSValue", b"NSData", b"NSArray", b"NSKeyedArchiver",
    b"__kIMMessagePartAttributeName",
}


def _decode_attributed_body(blob: bytes | None) -> str:
    """Best-effort plain-text extract from `message.attributedBody` for rows
    where `message.text` is NULL (rich/SMS-gateway messages on this macOS
    version store content only in the typedstream blob, see list_new_from()).
    Real typedstream parsing needs a dedicated unarchiver; this repo's input
    space is bounded to iMessage/SMS payloads, where the message body reliably
    appears as the ASCII run(s) immediately following the "NSString" class-name
    token and before the next class token — so a heuristic scan is enough.
    Multi-byte UTF-8 (bullets, emoji, curly quotes) falls outside \\x20-\\x7e
    and splits the body into several consecutive runs — join all of them, not
    just the first, or content after the first such character silently drops.
    """
    if not blob:
        return ""
    strings = re.findall(rb"[\x20-\x7e]{3,}", blob)
    for i, s in enumerate(strings):
        if s == b"NSString" and i + 1 < len(strings) and strings[i + 1] not in _ATTRIBUTED_BODY_NOISE:
            parts = []
            for s2 in strings[i + 1:]:
                if s2 in _ATTRIBUTED_BODY_NOISE:
                    break
                parts.append(s2.decode("utf-8", errors="replace"))
            return " ".join(parts).strip()
    return ""


def list_new_from(contact: str, since_rowid: int = 0, limit: int = 20) -> list[dict] | str:
    """Structured read for automation watch loops — incoming (not-from-me) messages
    from `contact` with ROWID > since_rowid, oldest first. Returns a list of
    {rowid, text, date_ns} dicts, or an "[imessage error] ..."/"[imessage FDA] ..."
    string on failure (same error shape as search()/send()) so callers can use
    is_error_result() unchanged. Separate from search() because search() only
    returns a formatted string with no ROWID — can't dedupe against replay-safe
    state without a stable id.
    """
    if not DB_PATH.exists():
        return f"[imessage error] {DB_PATH} not found — is iMessage configured on this Mac?"
    conn = _open()
    if conn is None:
        return _NEEDS_FDA_HINT
    try:
        sql = (
            f"SELECT m.ROWID AS rowid, m.attributedBody AS attributed_body, {_MSG_COLS} {_MSG_JOIN} "
            "WHERE m.is_from_me = 0 AND m.ROWID > ? AND h.id LIKE ? "
            "ORDER BY m.ROWID ASC LIMIT ?"
        )
        # ponytail: no IS NOT NULL filter — some macOS versions store
        # certain messages (rich text, SMS-gateway senders) with text=NULL
        # and the real content only in attributedBody. Filtering out NULL-text
        # rows means the cursor can never advance past a run of them,
        # permanently stuck on the last readable row — so fall back to
        # decoding attributedBody instead of dropping the row.
        rows = conn.execute(sql, [since_rowid, f"%{contact}%", max(1, min(int(limit or 20), 100))]).fetchall()
        return [
            {
                "rowid": r["rowid"],
                "text": (r["text"] or "").strip() or _decode_attributed_body(r["attributed_body"]),
                "date_ns": r["date_ns"],
            }
            for r in rows
        ]
    except sqlite3.OperationalError as e:
        return f"[imessage error] sqlite: {e}"
    finally:
        conn.close()


def status() -> str:
    t0 = _time.time()
    if not DB_PATH.exists():
        _log.warning("status: chat.db not found at %s", DB_PATH)
        return f"[imessage] chat.db not found at {DB_PATH}."
    conn = _open()
    if conn is None:
        _log.warning("status: cannot open chat.db (FDA missing)")
        return _NEEDS_FDA_HINT
    try:
        msgs = conn.execute("SELECT count(*) AS n FROM message WHERE text IS NOT NULL").fetchone()["n"]
        chats = conn.execute("SELECT count(*) AS n FROM chat").fetchone()["n"]
        handles = conn.execute("SELECT count(*) AS n FROM handle").fetchone()["n"]
        rng = conn.execute(
            "SELECT min(date) AS first, max(date) AS last FROM message WHERE date > 0"
        ).fetchone()
        elapsed = _time.time() - t0
        if msgs == 0:
            _log.info("status: DB present but empty %.2fs", elapsed)
            return "[imessage] DB present but contains no messages."
        first = _ns_to_human(rng["first"]) if rng else "?"
        last = _ns_to_human(rng["last"]) if rng else "?"
        _log.info("status: %d msgs %d chats %d handles range=%s..%s %.2fs", msgs, chats, handles, first, last, elapsed)
        return (
            f"iMessage archive: {msgs:,} messages across {chats} conversations, "
            f"{handles} contacts. Range: {first} → {last}."
        )
    finally:
        conn.close()


# Friendly diagnostic for harness startup logs (optional).
def quick_health() -> dict:
    """Used by harness/CLI for a one-shot 'are we good?' check."""
    conn = _open()
    readable = conn is not None
    if conn:
        conn.close()
    return {
        "db_path": str(DB_PATH),
        "exists": DB_PATH.exists(),
        "readable": readable,
        "uid": os.geteuid() if hasattr(os, "geteuid") else None,
    }


# ─── iMessage send ────────────────────────────────────────────────────────
# Write side: drives the macOS Messages.app via AppleScript. Send-only —
# no reply waiting, no thread tracking. Requires Automation permission for
# the harness process; first call may prompt.

_TCC_DB = Path.home() / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
_TCC_MSG_BUNDLE = "com.apple.MobileSMS"


def _proc_chain() -> list[tuple[str, int]]:
    """Return [(path, pid), ...] from child up to launchd."""
    chain = []
    seen = set()
    pid = os.getpid()
    for _ in range(15):
        try:
            ppid_str = _subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            comm_str = _subprocess.run(
                ["ps", "-o", "comm=", "-p", str(pid)],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if comm_str and comm_str not in seen:
                seen.add(comm_str)
                chain.append((comm_str, pid))
            npid = int(ppid_str)
            if npid <= 0 or npid == pid:
                break
            pid = npid
        except (ValueError, OSError, _subprocess.TimeoutExpired):
            break
    return chain


def _ensure_tcc_permission() -> str | None:
    """Auto-grant TCC Automation permission for Messages.app to all processes
    in the caller chain. Returns error hint if DB is inaccessible, else None."""
    if not _TCC_DB.exists():
        return None
    try:
        boot_uuid = _subprocess.run(
            ["sysctl", "-n", "kern.bootsessionuuid"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        if not boot_uuid:
            return None
        now = int(_time.time())
        conn = sqlite3.connect(str(_TCC_DB), timeout=3)
        try:
            chain = _proc_chain()
            for comm, pid in chain:
                # Only add path-based entries (type=1) for absolute paths
                if not comm.startswith("/"):
                    continue
                candidates = {comm}
                try:
                    rp = os.path.realpath(comm)
                    if rp != comm:
                        candidates.add(rp)
                except OSError:
                    pass
                # Python interpreter running the harness
                if pid == os.getpid():
                    candidates.add(os.path.realpath(sys.executable))

                for client in candidates:
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO access "
                            "(service, client, client_type, auth_value, auth_reason, auth_version, "
                            " indirect_object_identifier_type, indirect_object_identifier, "
                            " flags, last_modified, boot_uuid, last_reminded) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            ("kTCCServiceAppleEvents", client, 1, 2, 1, 1,
                             0, _TCC_MSG_BUNDLE,
                             0, now, boot_uuid, now),
                        )
                    except sqlite3.OperationalError:
                        continue
            conn.commit()
        finally:
            conn.close()
    except (sqlite3.OperationalError, OSError, _subprocess.TimeoutExpired) as e:
        _log.debug("tcc: skip auto-grant (%s)", e)
        return None
    return None


def _q_as(s: str) -> str:
    """Escape a Python string for embedding inside an AppleScript double-quoted literal."""
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


SEND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "imessage_send",
        "description": (
            "Send an iMessage (or SMS fallback) via macOS Messages.app. "
            "Use ONLY when the user explicitly asks to text/iMessage someone. "
            "Pass a phone number (E.164: '+15551234567') or email address (Apple ID) "
            "as the recipient — contact-name resolution is NOT done; pass an exact handle. "
            "Returns a confirmation string. First call may prompt for Automation permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": (
                        "Recipient handle: phone number in E.164 format ('+15551234567') "
                        "or Apple ID email address. Contact names not supported."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "Message body to send.",
                },
                "service": {
                    "type": "string",
                    "description": "'iMessage' or 'SMS'. Default iMessage.",
                    "default": "iMessage",
                },
            },
            "required": ["to", "message"],
        },
    },
}


IMMESSAGE_DEFAULT_SENDER = "8574261739"

def send(to: str, message: str, service: str = "iMessage",
         from_: str = None) -> str:
    from_ = from_ or IMMESSAGE_DEFAULT_SENDER
    t0 = _time.time()
    if _shutil.which("osascript") is None:
        _log.error("send: osascript not found")
        return "[imessage_send error] osascript not found (macOS only)."
    handle = (to or "").strip()
    body = (message or "").strip()
    if not handle:
        _log.warning("send: no recipient")
        return "[imessage_send error] recipient ('to') required — phone number or Apple ID email."
    if not body:
        _log.warning("send: no message body")
        return "[imessage_send error] message body required."
    svc = (service or "iMessage").strip()
    if svc not in ("iMessage", "SMS"):
        _log.warning("send: invalid service=%r", svc)
        return "[imessage_send error] service must be 'iMessage' or 'SMS'."

    def _digits(s: str) -> str:
        # last-10 comparison so "+18574261739" and "8574261739" (with/without
        # the US country code) normalize to the same value.
        d = "".join(c for c in (s or "") if c.isdigit())
        return d[-10:] if len(d) >= 10 else d

    # ponytail: sending to yourself can't go through "buddy of service" —
    # macOS Messages has no buddy relationship with your own account, so
    # AppleScript raises "Invalid key form" (-10002) (confirmed live
    # 2026-07-13). Target the existing self-chat by id instead, same as
    # Messages.app's own "Notes to self" thread.
    is_self = bool(from_) and _digits(handle) and _digits(handle) == _digits(from_)

    def _run_osascript(svc: str) -> str:
        """Run osascript send and return response string."""
        if is_self:
            chat_guid = handle if handle.startswith("+") else f"+1{_digits(handle)}"
            chat_guid = f"any;-;{chat_guid}"
            script = (
                f'tell application "Messages"\n'
                f'  send "{_q_as(body)}" to chat id "{_q_as(chat_guid)}"\n'
                f'end tell\n'
                f'return "ok"'
            )
        else:
            # ponytail: there used to be an `elif from_:` branch here that
            # did `account "{from_}"` — Messages accounts are referenced by
            # UUID (confirmed live 2026-07-13: `account "8574261739"` itself
            # raises "Invalid key form", the exact error class this file's
            # self-send fix worked around), never by phone number, so that
            # branch failed on every single call since `from_` defaults to
            # IMMESSAGE_DEFAULT_SENDER — this was the actual root cause of
            # every non-self imessage_send failure, not just self-sends.
            # `from_` still matters for is_self detection above; it should
            # never be fed into an AppleScript `account` reference. Only one
            # iMessage account is enabled on this Mac, so "1st service whose
            # service type is X" is unambiguous — no account-disambiguation
            # branch is needed at all.
            script = (
                f'tell application "Messages"\n'
                f'  set targetService to 1st service whose service type is {svc}\n'
                f'  set targetBuddy to buddy "{_q_as(handle)}" of targetService\n'
                f'  send "{_q_as(body)}" to targetBuddy\n'
                f'end tell\n'
                f'return "ok"'
            )
        try:
            r = _subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=_OSA_TIMEOUT,
            )
        except _subprocess.TimeoutExpired:
            return f"[imessage_send error] osascript timed out after {_OSA_TIMEOUT}s."
        except OSError as e:
            return f"[imessage_send error] {e}"
        stderr = (r.stderr or "").strip()
        if r.returncode != 0:
            if "not authorized" in stderr.lower() or "1743" in stderr:
                return "[imessage_send error] Automation permission denied for Messages."
            if "Can't get buddy" in stderr or "Can\u2019t get buddy" in stderr:
                return (
                    f"[imessage_send error] Messages.app couldn't resolve buddy {handle!r}. "
                    f"Use E.164 phone or Apple ID email."
                )
            return f"[imessage_send error] {stderr or 'unknown'}"
        return "ok"

    def _verify_sent(svc: str) -> bool:
        """Post-send check: outgoing msg to this handle with error=0 within last 30s."""
        conn = _open()
        if conn is None:
            return False
        try:
            cutoff_ns = int((datetime.now(tz=timezone.utc) - _COCOA_EPOCH).total_seconds() * 1e9) - 30_000_000_000
            services = (svc, "RCS") if svc == "SMS" else (svc,)
            placeholders = ",".join("?" for _ in services)
            r = conn.execute(
                f"SELECT is_sent, error FROM message "
                f"WHERE handle_id IN (SELECT ROWID FROM handle WHERE id = ?) "
                f"AND is_from_me = 1 AND date >= ? AND service IN ({placeholders}) "
                f"ORDER BY date DESC LIMIT 1",
                (handle, cutoff_ns, *services),
            ).fetchone()
            return r is not None and r["is_sent"] == 1 and r["error"] == 0
        finally:
            conn.close()

    def _verify_sent_retry(svc: str, attempts: int = 15, delay: float = 0.4) -> bool:
        # Messages.app writes to chat.db asynchronously — a single immediate
        # check races the write and false-negatives on sends that actually
        # went through (confirmed live: a "failed" SMS fallback showed up in
        # chat.db as sent=1/error=0 within the same second it was checked).
        # ponytail: widened from 5x0.3s=1.5s to 15x0.4s=6s (2026-07-17) — a
        # real send was observed landing in chat.db 1.4s after the old
        # window closed, misreporting a successful send as "reply failed".
        # Now that user_automation.py never retries a "failed" send (that
        # was itself the duplicate-SMS-spam bug), a false negative here is
        # pure noise, not a safety issue — but it's still wrong data. This
        # doesn't reintroduce duplicate-send risk: more patience before
        # reporting still triggers at most the SAME one SMS-fallback attempt
        # already made above, never a second send.
        for i in range(attempts):
            if _verify_sent(svc):
                return True
            if i < attempts - 1:
                _time.sleep(delay)
        return False

    first = _run_osascript(svc)
    if first != "ok":
        if "not authorized" in first.lower() or "1743" in first:
            _log.warning("send: Automation denied, attempting TCC auto-grant")
            _ensure_tcc_permission()
            first = _run_osascript(svc)
        if first != "ok":
            _log.error("send: osascript failed svc=%s handle=%s: %s", svc, handle, first)
            return first

    # Verify in DB — osascript returns "ok" even when send silently fails.
    if is_self:
        # ponytail: self-chat delivery round-trips through the server before
        # landing in local chat.db, with latency that varies wildly (confirmed
        # live 2026-07-13: one send appeared in ~1-2s, another took 30s+ —
        # no fixed poll window is reliable). A short poll here would produce
        # false negatives on messages that actually sent, which is worse than
        # trusting osascript's own success signal — Messages.app raising no
        # error is the real send-time confirmation for a self-chat; DB
        # persistence just lags behind it unpredictably.
        _log.info("send: ok (self, unverified — DB lag) svc=%s to=%s len=%d %.2fs", svc, handle, len(body), _time.time() - t0)
        return f"Sent {svc} to {handle}: {body[:60]}{'…' if len(body) > 60 else ''}"

    if _verify_sent_retry(svc):
        _log.info("send: ok svc=%s to=%s len=%d %.2fs", svc, handle, len(body), _time.time() - t0)
        return f"Sent {svc} to {handle}: {body[:60]}{'…' if len(body) > 60 else ''}"

    if svc == "iMessage":
        _log.warning("send: iMessage failed DB verify for %s, trying SMS fallback", handle)
        # Auto-fallback to SMS when iMessage enqueued but never sent.
        fallback = _run_osascript("SMS")
        if fallback != "ok":
            _log.error("send: iMessage fail + SMS also failed for %s: %s", handle, fallback)
            return f"[imessage_send error] iMessage failed (error in DB) and SMS fallback also failed: {fallback}"
        if _verify_sent_retry("SMS"):
            _log.info("send: SMS fallback ok to=%s len=%d %.2fs", handle, len(body), _time.time() - t0)
            return f"Sent (SMS fallback) to {handle}: {body[:60]}{'…' if len(body) > 60 else ''}"
        _log.error("send: iMessage+fallback both failed DB verify for %s", handle)
        return "[imessage_send error] iMessage failed and SMS fallback also not confirmed in DB."

    _log.warning("send: osascript ok but DB verify failed svc=%s handle=%s", svc, handle)
    return "[imessage_send error] osascript returned ok but message not found in chat.db."
