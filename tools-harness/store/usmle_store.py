"""
USMLE practice-question dedup + history — one sqlite row per generated
question, keyed by a hash of the topic+vignette text. Prevents the same
question (or a near-identical LLM regen on the same topic) from being resent,
and gives a persisted log of what was actually sent.
"""
import hashlib
import sqlite3
import time
from store import dbclose
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "data" / "usmle_store.sqlite"


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = dbclose.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS questions ("
        "hash TEXT PRIMARY KEY, topic TEXT NOT NULL, question TEXT NOT NULL, "
        "sent_at REAL NOT NULL)"
    )
    return conn


def content_hash(topic: str, vignette: str) -> str:
    return hashlib.sha256(f"{topic}|{vignette}".encode()).hexdigest()[:16]


def seen(chash: str) -> bool:
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM questions WHERE hash = ?", (chash,)).fetchone()
    return row is not None


def record(chash: str, topic: str, question: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO questions (hash, topic, question, sent_at) VALUES (?, ?, ?, ?)",
            (chash, topic, question, time.time()),
        )


def recent(limit: int = 20) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT hash, topic, question, sent_at FROM questions ORDER BY sent_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"hash": r[0], "topic": r[1], "question": r[2], "sent_at": r[3]} for r in rows]
