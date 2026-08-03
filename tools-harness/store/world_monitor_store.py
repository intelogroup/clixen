"""World monitor persistence — raw scan data, deduped findings, keyword feedback.

Tables:
  raw_scans       — raw text from each source per scan (retention: 7 days)
  findings        — deduped findings that were surfaced (content_hash unique)
  keyword_scores  — keywords extracted from gate-passing findings, scored by hit rate
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from store import dbclose
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "world_monitor.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS raw_scans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at    TEXT NOT NULL,
    source        TEXT NOT NULL,
    raw_text      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS findings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash  TEXT NOT NULL UNIQUE,
    finding       TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    source_used   TEXT NOT NULL DEFAULT '',
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    notified      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_findings_hash ON findings(content_hash);
CREATE INDEX IF NOT EXISTS idx_findings_notified ON findings(notified);

CREATE TABLE IF NOT EXISTS keyword_scores (
    keyword       TEXT PRIMARY KEY,
    hits          INTEGER NOT NULL DEFAULT 0,
    misses        INTEGER NOT NULL DEFAULT 0,
    last_seen     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_hits (
    source        TEXT PRIMARY KEY,
    total_scans   INTEGER NOT NULL DEFAULT 0,
    findings_generated INTEGER NOT NULL DEFAULT 0,
    last_scan     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = dbclose.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _conn() as conn:
        conn.executescript(_CREATE)


def save_raw_scan(source: str, raw_text: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO raw_scans (scanned_at, source, raw_text) VALUES (?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), source, raw_text),
        )


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# LLM rewords the finding headline every scan (arXiv feed is stable, the text
# is not) — hashing the raw text made every reword look "new". Hash a
# normalized first-N-chars key instead: stable across rewording/truncation
# ("...Tayler-Spruit dynamo." vs "...Tayler-Spruit dynamo: Global simulations
# reveal the formation..."), still distinguishes unrelated findings.
DEDUP_KEY_CHARS = 80


def dedup_key(finding_text: str) -> str:
    norm = re.sub(r"\s+", " ", (finding_text or "").strip().lower())
    # LLM appends/truncates a subtitle after the stable title (": Global
    # simulations reveal...") — key on the lead phrase before the first
    # separator (".", ":", ";", "—", etc.), capped to bound collisions.
    cut = re.split(r"[.:;!?—]|\s-\s", norm)[0]
    return cut.strip()[:DEDUP_KEY_CHARS]


# Prefix-key dedup misses reworks where the LLM moves the subject out of the
# lead phrase ("Quantum Fidelity-per-Cost: ..." vs "A new metric, Quantum
# Fidelity-per-Cost, is proposed..." vs "New metric 'Quantum Fidelity-per-Cost'
# developed...") — same story, different sentence shape each time, hashed 40x
# distinct. Fall back to significant-word overlap against recent findings.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "for", "of",
    "to", "in", "on", "with", "and", "or", "new", "novel", "this", "that",
    "has", "have", "had", "as", "by", "at", "from", "into", "its", "their",
}
SIMILARITY_WINDOW_DAYS = 14
SIMILARITY_THRESHOLD = 0.52


def _word_set(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) >= 4 and w not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_known(finding_text: str) -> bool:
    h = content_hash(dedup_key(finding_text))
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM findings WHERE content_hash=?", (h,)).fetchone()
        if row is not None:
            return True
        target = _word_set(finding_text)
        if not target:
            return False
        cutoff = (datetime.now(timezone.utc) - timedelta(days=SIMILARITY_WINDOW_DAYS)).isoformat()
        rows = conn.execute(
            "SELECT finding FROM findings WHERE last_seen >= ?", (cutoff,)
        ).fetchall()
        for r in rows:
            if _jaccard(target, _word_set(r["finding"])) >= SIMILARITY_THRESHOLD:
                return True
        return False


def mark_notified(finding_text: str, reason: str, source_used: str) -> None:
    h = content_hash(dedup_key(finding_text))
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO findings (content_hash, finding, reason, source_used, first_seen, last_seen, notified)
               VALUES (?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(content_hash) DO UPDATE SET last_seen=excluded.last_seen, notified=1""",
            (h, finding_text, reason, source_used, now, now),
        )


def cleanup_old_scans(days: int = 7) -> None:
    with _conn() as conn:
        conn.execute(
            "DELETE FROM raw_scans WHERE scanned_at < ?",
            (datetime.now(timezone.utc).isoformat(),),
        )


def record_keyword_hit(keyword: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO keyword_scores (keyword, hits, misses, last_seen) VALUES (?, 1, 0, ?) "
            "ON CONFLICT(keyword) DO UPDATE SET hits=hits+1, last_seen=excluded.last_seen",
            (keyword.lower().strip(), now),
        )


def record_keyword_miss(keyword: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO keyword_scores (keyword, hits, misses, last_seen) VALUES (?, 0, 1, ?) "
            "ON CONFLICT(keyword) DO UPDATE SET misses=misses+1, last_seen=excluded.last_seen",
            (keyword.lower().strip(), now),
        )


def record_source_scan(source: str, findings_count: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO source_hits (source, total_scans, findings_generated, last_scan)
               VALUES (?, 1, ?, ?)
               ON CONFLICT(source) DO UPDATE SET
                 total_scans=total_scans+1,
                 findings_generated=findings_generated+excluded.findings_generated,
                 last_scan=excluded.last_scan""",
            (source, findings_count, now),
        )


def top_keywords(limit: int = 20, min_hits: int = 1) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT keyword, hits, misses, CAST(hits AS REAL) / MAX(hits + misses, 1) AS hit_rate
               FROM keyword_scores WHERE hits >= ? ORDER BY hits DESC LIMIT ?""",
            (min_hits, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def source_performance() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT source, total_scans, findings_generated,
                      CAST(findings_generated AS REAL) / MAX(total_scans, 1) AS hit_rate
               FROM source_hits ORDER BY hit_rate DESC""",
        ).fetchall()
        return [dict(r) for r in rows]


def set_evolved_queries(queries_json: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES (?, ?, ?)",
            ("evolved_arxiv_queries", queries_json, now),
        )


def get_evolved_queries() -> list[str]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key=?", ("evolved_arxiv_queries",)
        ).fetchone()
    if row:
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def stale_keywords(days: int = 14) -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT keyword FROM keyword_scores WHERE last_seen < ?",
            (datetime.now(timezone.utc).isoformat(),),
        ).fetchall()
        return [r[0] for r in rows]


def stats() -> dict:
    with _conn() as conn:
        scans = conn.execute("SELECT COUNT(*), COUNT(DISTINCT source) FROM raw_scans").fetchone()
        finds = conn.execute("SELECT COUNT(*) FROM findings").fetchone()
        notified = conn.execute("SELECT COUNT(*) FROM findings WHERE notified=1").fetchone()
        kw = conn.execute("SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM keyword_scores").fetchone()
        return {
            "raw_scans": scans[0],
            "sources": scans[1],
            "total_findings": finds[0],
            "notified": notified[0],
            "keywords_tracked": kw[0],
            "total_keyword_hits": kw[1],
        }
