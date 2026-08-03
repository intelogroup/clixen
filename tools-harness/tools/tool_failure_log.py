"""
Persistent record of repeated tool-call failures, keyed by (tool_name, normalized error).

Lets execute_tool() warn the model when it's about to retry something that has
failed the same way N+ times before, instead of only having this documented as
prose in CLAUDE.md. See jobs/job_queue.py for the sqlite conventions this mirrors.
"""

import re
import sqlite3
from store import dbclose
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "_tool_failures.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS tool_failures (
    tool_name  TEXT NOT NULL,
    error_sig  TEXT NOT NULL,
    count      INTEGER NOT NULL DEFAULT 0,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (tool_name, error_sig)
);
"""

_HINT_THRESHOLD = 3

_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_PATH_RE = re.compile(r"/\S+")
_NUM_RE = re.compile(r"\b\d+\b")
_WS_RE = re.compile(r"\s+")


def _conn() -> sqlite3.Connection:
    con = dbclose.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init() -> None:
    with _conn() as con:
        con.executescript(_CREATE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_error(error_text: str) -> str:
    """Strip paths/quoted-values/numbers so the same failure shape re-matches."""
    sig = _QUOTED_RE.sub("''", error_text)
    sig = _PATH_RE.sub("<path>", sig)
    sig = _NUM_RE.sub("<n>", sig)
    sig = _WS_RE.sub(" ", sig).strip()
    return sig[:200]


def record_failure(tool_name: str, error_text: str) -> int:
    """Upsert the failure count for this (tool, normalized error). Returns new count."""
    error_sig = normalize_error(error_text)
    now = _now()
    with _conn() as con:
        con.execute(
            """
            INSERT INTO tool_failures (tool_name, error_sig, count, last_seen)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(tool_name, error_sig) DO UPDATE SET
                count = count + 1,
                last_seen = excluded.last_seen
            """,
            (tool_name, error_sig, now),
        )
        row = con.execute(
            "SELECT count FROM tool_failures WHERE tool_name = ? AND error_sig = ?",
            (tool_name, error_sig),
        ).fetchone()
        return row["count"] if row else 1


def get_hint(tool_name: str) -> str | None:
    """Return a corrective hint if this tool has a failure pattern past threshold, else None."""
    with _conn() as con:
        row = con.execute(
            """
            SELECT error_sig, count FROM tool_failures
            WHERE tool_name = ? AND count >= ?
            ORDER BY count DESC LIMIT 1
            """,
            (tool_name, _HINT_THRESHOLD),
        ).fetchone()
    if not row:
        return None
    return (
        f"Note: {tool_name} has repeatedly failed ({row['count']}x) with a similar error "
        f"('{row['error_sig']}') in past runs — stop retrying the same approach; "
        f"try a different strategy or ask the user for guidance."
    )


init()
