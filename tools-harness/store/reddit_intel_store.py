"""
Reddit market-intelligence persistence — raw items, merged insights, immutable
join log. Modeled on store/workflow_store.py (same SQLite-per-file pattern).

Three layers, per design: raw_items is the never-summarized archive; insights
are the few clustered pain-points the LLM actually reads; item_insight_links
is an append-only audit trail (never UPDATE/DELETE — a row records one merge
decision forever, so "why does this insight exist" is always answerable).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from store import dbclose
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "reddit_intel.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS raw_items (
    id            TEXT PRIMARY KEY,   -- reddit fullname, e.g. t3_abc123
    subreddit     TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL DEFAULT '',
    content_hash  TEXT NOT NULL,
    score         INTEGER NOT NULL DEFAULT 0,
    num_comments  INTEGER NOT NULL DEFAULT 0,
    embedding_id  TEXT NOT NULL DEFAULT '',
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_items_hash ON raw_items(content_hash);

CREATE TABLE IF NOT EXISTS insights (
    id                  TEXT PRIMARY KEY,
    summary             TEXT NOT NULL,
    evidence_count      INTEGER NOT NULL DEFAULT 1,
    subreddits_json     TEXT NOT NULL DEFAULT '[]',
    severity            REAL NOT NULL DEFAULT 0,
    willingness_to_pay  REAL NOT NULL DEFAULT 0,
    trend_velocity      REAL NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'active',  -- active | rejected | merged
    embedding_id        TEXT NOT NULL DEFAULT '',
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_insight_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL,
    insight_id  TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS watched_subreddits (
    name                TEXT PRIMARY KEY,
    status              TEXT NOT NULL DEFAULT 'active',  -- active | dropped
    scans_since_new     INTEGER NOT NULL DEFAULT 0,
    last_new_insight_at TEXT,
    added_at            TEXT NOT NULL,
    discovered_reason   TEXT NOT NULL DEFAULT ''
);
"""


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


def content_hash(title: str, body: str) -> str:
    normalized = (title.strip() + "\n" + body.strip()).lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


# ── raw_items ────────────────────────────────────────────────────────────

def item_exists(item_id: str) -> bool:
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM raw_items WHERE id = ?", (item_id,)).fetchone()
        return row is not None


def find_by_content_hash(chash: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM raw_items WHERE content_hash = ?", (chash,)
        ).fetchone()
        return dict(row) if row else None


def touch_item(item_id: str) -> None:
    """Exact-dupe or re-seen item: bump last_seen only, no new row."""
    with _conn() as conn:
        conn.execute("UPDATE raw_items SET last_seen = ? WHERE id = ?", (_now(), item_id))
        conn.commit()


def insert_item(
    item_id: str, subreddit: str, title: str, body: str, url: str,
    score: int, num_comments: int, embedding_id: str = "",
) -> None:
    now = _now()
    with _conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO raw_items
               (id, subreddit, title, body, url, content_hash, score, num_comments,
                embedding_id, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, subreddit, title, body, url, content_hash(title, body),
             score, num_comments, embedding_id, now, now),
        )
        conn.commit()


# ── insights ─────────────────────────────────────────────────────────────

def create_insight(
    summary: str, subreddits: list[str], severity: float,
    willingness_to_pay: float, embedding_id: str = "",
) -> str:
    insight_id = uuid.uuid4().hex
    now = _now()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO insights
               (id, summary, evidence_count, subreddits_json, severity,
                willingness_to_pay, trend_velocity, status, embedding_id,
                first_seen, last_seen)
               VALUES (?, ?, 1, ?, ?, ?, 0, 'active', ?, ?, ?)""",
            (insight_id, summary, json.dumps(subreddits), severity,
             willingness_to_pay, embedding_id, now, now),
        )
        conn.commit()
    return insight_id


def bump_insight(insight_id: str, subreddit: str) -> None:
    """A new post matched an existing cluster — grow evidence, don't create a row."""
    with _conn() as conn:
        row = conn.execute("SELECT subreddits_json, evidence_count, first_seen FROM insights WHERE id = ?", (insight_id,)).fetchone()
        if row is None:
            return
        subs = set(json.loads(row["subreddits_json"]))
        subs.add(subreddit)
        now = _now()
        first_seen = row["first_seen"]
        days = max((datetime.fromisoformat(now) - datetime.fromisoformat(first_seen)).days, 1)
        new_count = row["evidence_count"] + 1
        conn.execute(
            """UPDATE insights SET evidence_count = ?, subreddits_json = ?,
               trend_velocity = ?, last_seen = ? WHERE id = ?""",
            (new_count, json.dumps(sorted(subs)), new_count / days, now, insight_id),
        )
        conn.commit()


def get_insight(insight_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM insights WHERE id = ?", (insight_id,)).fetchone()
        return dict(row) if row else None


def list_active_insights(limit: int = 200) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM insights WHERE status = 'active' ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── item_insight_links (append-only) ───────────────────────────────────

def link(item_id: str, insight_id: str, decision: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO item_insight_links (item_id, insight_id, decision, created_at) VALUES (?, ?, ?, ?)",
            (item_id, insight_id, decision, _now()),
        )
        conn.commit()


def link_count() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM item_insight_links").fetchone()[0]


def list_links_by_decision(decision: str, since: str, limit: int = 100) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM item_insight_links WHERE decision = ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT ?",
            (decision, since, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ── watched_subreddits (which subs to scan, and whether they're still worth it) ─

def seed_watched_subreddits(names: list[str]) -> None:
    now = _now()
    with _conn() as conn:
        for name in names:
            conn.execute(
                """INSERT OR IGNORE INTO watched_subreddits
                   (name, status, scans_since_new, added_at, discovered_reason)
                   VALUES (?, 'active', 0, ?, 'seed')""",
                (name, now),
            )
        conn.commit()


def add_subreddit(name: str, reason: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO watched_subreddits
               (name, status, scans_since_new, added_at, discovered_reason)
               VALUES (?, 'active', 0, ?, ?)""",
            (name, _now(), reason),
        )
        conn.commit()


def list_active_subreddits() -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT name FROM watched_subreddits WHERE status = 'active'"
        ).fetchall()
        return [r["name"] for r in rows]


def record_scan_outcome(name: str, got_signal: bool) -> None:
    """got_signal=True means this scan produced a CREATE or UPDATE for this
    subreddit — resets the dead-scan counter. False increments it."""
    with _conn() as conn:
        if got_signal:
            conn.execute(
                "UPDATE watched_subreddits SET scans_since_new = 0, last_new_insight_at = ? WHERE name = ?",
                (_now(), name),
            )
        else:
            conn.execute(
                "UPDATE watched_subreddits SET scans_since_new = scans_since_new + 1 WHERE name = ?",
                (name,),
            )
        conn.commit()


def get_scans_since_new(name: str) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT scans_since_new FROM watched_subreddits WHERE name = ?", (name,)
        ).fetchone()
        return row["scans_since_new"] if row else 0


def drop_subreddit(name: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE watched_subreddits SET status = 'dropped' WHERE name = ?", (name,))
        conn.commit()


# ── meta (small key/value, e.g. discovery cooldown timestamp) ─────────────

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


# ── config overrides (auto-tunable numeric knobs, JSON-encoded via meta) ──

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


# ── subagent_prompts (append-only, versioned — auto-rewrite must stay reversible) ──

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
