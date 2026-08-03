"""
Science-niche scout persistence — raw papers, merged claims, immutable join
log, niche performance tracking.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from store import dbclose
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "science_scout.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS niche_perf (
    niche         TEXT PRIMARY KEY,
    scans         INTEGER NOT NULL DEFAULT 0,
    hits          INTEGER NOT NULL DEFAULT 0,
    misses        INTEGER NOT NULL DEFAULT 0,
    last_scan     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_papers (
    id            TEXT PRIMARY KEY,   -- url (arxiv/pubmed/journal link)
    niche         TEXT NOT NULL,
    title         TEXT NOT NULL,
    snippet       TEXT NOT NULL DEFAULT '',
    extracted_claim TEXT NOT NULL DEFAULT '',  -- paper-qa full-text extraction
    url           TEXT NOT NULL DEFAULT '',
    content_hash  TEXT NOT NULL,
    embedding_id  TEXT NOT NULL DEFAULT '',
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_papers_hash ON raw_papers(content_hash);

CREATE TABLE IF NOT EXISTS claims (
    id                  TEXT PRIMARY KEY,
    summary             TEXT NOT NULL,
    evidence_level      TEXT NOT NULL DEFAULT 'Speculative',
    -- Observed | Replicated | Mechanistically supported | Predicted | Speculative | Impossible
    evidence_count      INTEGER NOT NULL DEFAULT 1,
    niches_json         TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'active',  -- active | rejected | merged
    embedding_id        TEXT NOT NULL DEFAULT '',
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_claim_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id    TEXT NOT NULL,
    claim_id    TEXT NOT NULL,
    decision    TEXT NOT NULL,  -- CREATE | UPDATE | IGNORE | CONTRADICT
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subagent_prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT 'human'
);

CREATE INDEX IF NOT EXISTS idx_subagent_prompts_name ON subagent_prompts(name);

CREATE TABLE IF NOT EXISTS suppressed_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    finding     TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
"""

VALID_EVIDENCE_LEVELS = (
    "Observed", "Replicated", "Mechanistically supported",
    "Predicted", "Speculative", "Impossible",
)

_schema_ready_paths: set[str] = set()


def _conn() -> sqlite3.Connection:
    path = str(DB_PATH)
    conn = dbclose.connect(path)
    conn.row_factory = sqlite3.Row
    if path not in _schema_ready_paths:
        conn.executescript(_CREATE)
        _schema_ready_paths.add(path)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(title: str, url: str) -> str:
    normalized = (title.strip() + "\n" + url.strip()).lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


# ── raw_papers ───────────────────────────────────────────────────────────

def paper_exists(paper_id: str) -> bool:
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM raw_papers WHERE id = ?", (paper_id,)).fetchone()
        return row is not None


def find_by_content_hash(chash: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM raw_papers WHERE content_hash = ?", (chash,)).fetchone()
        return dict(row) if row else None


def touch_paper(paper_id: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE raw_papers SET last_seen = ? WHERE id = ?", (_now(), paper_id))
        conn.commit()


def insert_paper(
    paper_id: str, niche: str, title: str, snippet: str, url: str,
    extracted_claim: str = "", embedding_id: str = "",
) -> None:
    now = _now()
    with _conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO raw_papers
               (id, niche, title, snippet, extracted_claim, url, content_hash,
                embedding_id, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (paper_id, niche, title, snippet, extracted_claim, url,
             content_hash(title, url), embedding_id, now, now),
        )
        conn.commit()


# ── claims ───────────────────────────────────────────────────────────────

def create_claim(
    summary: str, evidence_level: str, niches: list[str], embedding_id: str = "",
) -> str:
    if evidence_level not in VALID_EVIDENCE_LEVELS:
        evidence_level = "Speculative"
    claim_id = uuid.uuid4().hex
    now = _now()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO claims
               (id, summary, evidence_level, evidence_count, niches_json,
                status, embedding_id, first_seen, last_seen)
               VALUES (?, ?, ?, 1, ?, 'active', ?, ?, ?)""",
            (claim_id, summary, evidence_level, json.dumps(niches), embedding_id, now, now),
        )
        conn.commit()
    return claim_id


def bump_claim(claim_id: str, niche: str, evidence_level: str | None = None) -> None:
    """A new paper corroborated an existing claim — grow evidence, don't create a row."""
    with _conn() as conn:
        row = conn.execute("SELECT niches_json, evidence_count FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if row is None:
            return
        niches = set(json.loads(row["niches_json"]))
        niches.add(niche)
        new_count = row["evidence_count"] + 1
        if evidence_level and evidence_level in VALID_EVIDENCE_LEVELS:
            conn.execute(
                """UPDATE claims SET evidence_count = ?, niches_json = ?,
                   evidence_level = ?, last_seen = ? WHERE id = ?""",
                (new_count, json.dumps(sorted(niches)), evidence_level, _now(), claim_id),
            )
        else:
            conn.execute(
                "UPDATE claims SET evidence_count = ?, niches_json = ?, last_seen = ? WHERE id = ?",
                (new_count, json.dumps(sorted(niches)), _now(), claim_id),
            )
        conn.commit()


def get_claim(claim_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        return dict(row) if row else None


def list_active_claims(limit: int = 200) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM claims WHERE status = 'active' ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── paper_claim_links (append-only) ─────────────────────────────────────

def link(paper_id: str, claim_id: str, decision: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO paper_claim_links (paper_id, claim_id, decision, created_at) VALUES (?, ?, ?, ?)",
            (paper_id, claim_id, decision, _now()),
        )
        conn.commit()


def list_links_by_decision(decision: str, since: str, limit: int = 100) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_claim_links WHERE decision = ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT ?",
            (decision, since, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ── meta (small key/value) ─────────────────────────────────────────────

def get_meta(key: str) -> str | None:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


# ── config overrides (auto-tunable knobs, JSON-encoded via meta) ──────────

_CONFIG_PREFIX = "config:"


def get_config(key: str, default):
    raw = get_meta(_CONFIG_PREFIX + key)
    return json.loads(raw) if raw is not None else default


def set_config(key: str, value) -> None:
    set_meta(_CONFIG_PREFIX + key, json.dumps(value))


# ── suppressed_alerts (persisted so the audit can review what got buried) ──

def log_suppressed_alert(source: str, finding: str, reason: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO suppressed_alerts (source, finding, reason, created_at) VALUES (?, ?, ?, ?)",
            (source, finding, reason, _now()),
        )
        conn.commit()


def list_suppressed_alerts(since: str, limit: int = 100) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM suppressed_alerts WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (since, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ── niche performance tracking ─────────────────────────────────────────

def record_niche_perf(niche: str, had_finding: bool) -> None:
    now = _now()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO niche_perf (niche, scans, hits, misses, last_scan)
               VALUES (?, 1, ?, ?, ?)
               ON CONFLICT(niche) DO UPDATE SET
                 scans=scans+1,
                 hits=hits+excluded.hits,
                 misses=misses+excluded.misses,
                 last_scan=excluded.last_scan""",
            (niche, 1 if had_finding else 0, 0 if had_finding else 1, now),
        )


def top_niches(limit: int = 10) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT niche, scans, hits, misses,
                      CAST(hits AS REAL) / MAX(hits + misses, 1) AS hit_rate
               FROM niche_perf ORDER BY hit_rate DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def bottom_niches(limit: int = 5, min_scans: int = 3) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT niche, scans, hits, misses,
                      CAST(hits AS REAL) / MAX(hits + misses, 1) AS hit_rate
               FROM niche_perf WHERE scans >= ? ORDER BY hit_rate ASC LIMIT ?""",
            (min_scans, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ── subagent_prompts (append-only, versioned) ──────────────────────────

def get_prompt(name: str, default: str) -> str:
    with _conn() as conn:
        row = conn.execute(
            "SELECT text FROM subagent_prompts WHERE name = ? ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
        return row["text"] if row else default


def set_prompt(name: str, text: str, created_by: str = "human") -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM subagent_prompts WHERE name = ?", (name,)
        ).fetchone()
        next_version = (row["v"] or 0) + 1
        conn.execute(
            "INSERT INTO subagent_prompts (name, version, text, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
            (name, next_version, text, _now(), created_by),
        )
        conn.commit()
        return next_version


def rollback_prompt(name: str, to_version: int) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT text FROM subagent_prompts WHERE name = ? AND version = ?", (name, to_version)
        ).fetchone()
        if row is None:
            return False
    set_prompt(name, row["text"], created_by="rollback")
    return True
