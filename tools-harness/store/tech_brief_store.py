"""
Tech briefing dedup + history — one sqlite row per headline pulled from HN/
TechCrunch, keyed by a hash of the headline text. Prevents the same story
surviving across the 7AM/6PM run pair (BrowserOS scrapes the same front page
twice a day, headlines overlap) from being re-summarized/re-emailed.
"""
import hashlib
import sqlite3
import time
from store import dbclose
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "data" / "tech_brief_store.sqlite"
_MAX_AGE_DAYS = 3  # headlines older than this stop being treated as dupes


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = dbclose.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS headlines ("
        "hash TEXT PRIMARY KEY, source TEXT NOT NULL, headline TEXT NOT NULL, "
        "sent_at REAL NOT NULL)"
    )
    return conn


def content_hash(headline: str) -> str:
    return hashlib.sha256(headline.strip().lower().encode()).hexdigest()[:16]


def dedupe_lines(source: str, lines: list[str]) -> list[str]:
    """Drop lines already sent within _MAX_AGE_DAYS, record the survivors as sent."""
    cutoff = time.time() - _MAX_AGE_DAYS * 86400
    fresh = []
    with _conn() as conn:
        conn.execute("DELETE FROM headlines WHERE sent_at < ?", (cutoff,))
        for line in lines:
            if not line.strip():
                continue
            chash = content_hash(line)
            row = conn.execute("SELECT 1 FROM headlines WHERE hash = ?", (chash,)).fetchone()
            if row:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO headlines (hash, source, headline, sent_at) VALUES (?, ?, ?, ?)",
                (chash, source, line, time.time()),
            )
            fresh.append(line)
    return fresh


def recent(limit: int = 30) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT hash, source, headline, sent_at FROM headlines ORDER BY sent_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"hash": r[0], "source": r[1], "headline": r[2], "sent_at": r[3]} for r in rows]
