"""
PI-quantum tracker - persistent memory store (facts/learnings) + context folding.

Memory is a SQLite table of durable facts, recurring topics, and decisions that
persist across runs. The store dedups by content hash, tracks recency/seen-count,
and can fold (compress/summarize) so stale or low-value entries don't bloat the
context handed to the agent each run.

Two stores:
  memory    — durable facts/learnings (the long-term brain)
  state     — a single running "folded state" row: what we track, last run summary,
              cooldowns, so each run starts lean instead of full history.

CLI:
  python3 memory.py remember "fact text" --cat ai [--importance 3]
  python3 memory.py recall "query" [--limit 5]      # newest/important
  python3 memory.py fold "run summary text" [--topic ai]
  python3 memory.py state [--set ...]                 # read/write folded state
  python3 memory.py prune                              # drop stale/low-value
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "tracker.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(text: str) -> str:
    # normalize whitespace/case so near-identical facts dedup to one hash
    norm = re.sub(r"\s+", " ", text or "").strip().lower()
    return hashlib.sha256(norm.encode()).hexdigest()[:24]


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_hash TEXT UNIQUE,
        text TEXT,
        category TEXT DEFAULT 'general',
        source TEXT DEFAULT 'agent',
        importance INTEGER DEFAULT 1,
        seen_count INTEGER DEFAULT 1,
        last_seen TEXT,
        created_at TEXT,
        folded INTEGER DEFAULT 0,
        folded_from TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_memory_cat ON memory(category);
    CREATE TABLE IF NOT EXISTS state (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)
    conn.commit()
    return conn


# ---------------- memory ----------------

def remember(conn, text: str, category: str = "general", source: str = "agent",
             importance: int = 1, folded_from: str | None = None) -> str:
    h = _hash(text)
    now = _now()
    cur = conn.execute("SELECT id, seen_count FROM memory WHERE content_hash=?", (h,))
    row = cur.fetchone()
    if row:
        conn.execute(
            "UPDATE memory SET last_seen=?, seen_count=seen_count+1 WHERE id=?",
            (now, row["id"]))
        conn.commit()
        return f"bumped (seen {row['seen_count']+1}x)"
    conn.execute(
        "INSERT INTO memory (content_hash,text,category,source,importance,last_seen,created_at,folded_from) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (h, text, category, source, importance, now, now, folded_from))
    conn.commit()
    return "stored"


def recall(conn, limit: int = 8, category: str | None = None) -> list[dict]:
    sql = ("SELECT text,category,importance,seen_count,last_seen,created_at,folded "
           "FROM memory WHERE folded=0")
    args: list = []
    if category:
        sql += " AND category=?"
        args.append(category)
    sql += " ORDER BY importance DESC, last_seen DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


# ---------------- context folding ----------------

def fold(conn, summary: str, topic: str | None = None) -> str:
    """Fold a run summary into memory as a durable folded entry."""
    text = f"[fold:{topic or 'run'}] {summary}"
    now = _now()
    h = _hash(text)
    conn.execute(
        "INSERT OR IGNORE INTO memory (content_hash,text,category,source,importance,last_seen,created_at,folded) "
        "VALUES (?,?,'fold','agent',1,?,?,1)",
        (h, text, now, now))
    conn.commit()
    return "folded"


def get_state(conn, key: str) -> str | None:
    r = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def set_state(conn, key: str, value) -> None:
    conn.execute("INSERT INTO state(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value) if isinstance(value, (dict, list)) else str(value)))
    conn.commit()


def prune(conn, max_age_days: int = 45, min_importance: int = 1) -> int:
    """Drop folded/low-value entries older than max_age_days, cap folded count."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    cur = conn.execute(
        "DELETE FROM memory WHERE folded=1 AND created_at < ? AND importance < 3",
        (cutoff,))
    # keep only the 200 newest folded + 500 total
    conn.execute("DELETE FROM memory WHERE id NOT IN "
                 "(SELECT id FROM memory ORDER BY created_at DESC LIMIT 800)")
    n = cur.rowcount
    conn.commit()
    return n


# ---------------- CLI ----------------

def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("remember")
    p.add_argument("text")
    p.add_argument("--cat", default="general")
    p.add_argument("--source", default="agent")
    p.add_argument("--importance", type=int, default=1)

    p = sub.add_parser("recall")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--cat", default=None)

    p = sub.add_parser("fold")
    p.add_argument("summary")
    p.add_argument("--topic", default=None)

    p = sub.add_parser("state")
    p.add_argument("--get", default=None)
    p.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), default=None)

    p = sub.add_parser("prune")
    p.add_argument("--age", type=int, default=45)

    args = ap.parse_args()
    conn = connect()

    if args.cmd == "remember":
        print(remember(conn, args.text, args.cat, args.source, args.importance))
    elif args.cmd == "recall":
        for r in recall(conn, args.limit, args.cat):
            print(f"[{r['category']}|imp{r['importance']}|seen{r['seen_count']}] {r['text']}")
    elif args.cmd == "fold":
        print(fold(conn, args.summary, args.topic))
    elif args.cmd == "state":
        if args.get:
            print(get_state(conn, args.get) or "")
        elif args.set:
            set_state(conn, args.set[0], args.set[1])
            print("set")
        else:
            for r in conn.execute("SELECT key,value FROM state"):
                print(f"{r['key']}={r['value']}")
    elif args.cmd == "prune":
        print(f"pruned {prune(conn, args.age)}")


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        print(f"memory error: {e}", file=sys.stderr)
        raise SystemExit(1)
