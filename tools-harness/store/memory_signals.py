"""Recall usage signals for tools/memory_tools.py.

Every memory the similarity gate actually retrieves gets a row here: which query
wording pulled it, how well it matched, when. Nothing consumes these yet — this is
the data layer for ranking and pruning memories by demonstrated usefulness, which
recall_block() currently has no basis for: a fact pulled into 200 turns and a fact
pulled into one are indistinguishable to it, and both cost prompt tokens forever.

Sidecar sqlite rather than a KnowledgeBase column — see memory_tools._build_tags:
KnowledgeBase drops and recreates its table on any schema mismatch, which would wipe
every stored memory. Mirrors the memory_relations.db side-table already next to it.
"""
import hashlib
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_conn = None
_lock = threading.Lock()
_WS_RE = re.compile(r"\s+")


_SCHEMA = """
    CREATE TABLE IF NOT EXISTS recall_signals (
        mem_id      TEXT NOT NULL,
        query_hash  TEXT NOT NULL,
        recall_day  TEXT NOT NULL,
        hits        INTEGER NOT NULL,
        total_score REAL NOT NULL,
        first_at    TEXT NOT NULL,
        last_at     TEXT NOT NULL,
        PRIMARY KEY (mem_id, query_hash, recall_day)
    )
"""


def connect(path: str) -> sqlite3.Connection:
    """Open (and initialize) a signal store. ":memory:" gives tests a throwaway one."""
    con = sqlite3.connect(path, check_same_thread=False)
    con.execute(_SCHEMA)
    con.commit()
    return con


def _db():
    global _conn
    if _conn is None:
        from tools.vault_paths import db_path as _vault_db_path, ensure_data_dir
        ensure_data_dir()
        _conn = connect(_vault_db_path("memory_signals.db"))
    return _conn


def query_hash(query: str) -> str:
    """Stable id for a query's wording. Counting *distinct* hashes is what separates a
    broadly useful memory from one a single repeated question keeps dragging in."""
    normalized = _WS_RE.sub(" ", (query or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def record_recalls(query: str, recalled: list[tuple[str, float]]) -> None:
    """Record that `recalled` memories were injected for `query`.

    `recalled` is (mem_id, relevance), relevance 0..1 with 1 = exact match. One row per
    (memory, query wording, day); repeats inside that bump the counter, so the table
    grows with distinct usage rather than with turn count.

    Fail-silent: bookkeeping on a live turn is never worth raising into.
    """
    if not recalled:
        return
    now = datetime.now(timezone.utc)
    stamp, day = now.isoformat(), now.date().isoformat()
    qh = query_hash(query)
    try:
        with _lock:
            con = _db()
            con.executemany(
                """
                INSERT INTO recall_signals
                    (mem_id, query_hash, recall_day, hits, total_score, first_at, last_at)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT (mem_id, query_hash, recall_day) DO UPDATE SET
                    hits        = hits + 1,
                    total_score = total_score + excluded.total_score,
                    last_at     = excluded.last_at
                """,
                [(mem_id, qh, day, score, stamp, stamp) for mem_id, score in recalled],
            )
            con.commit()
    except Exception as e:
        log.debug("recall signal write skipped: %s", e)


def signals_for(mem_id: str = "") -> list[dict]:
    """Signal rows, newest first; all memories when mem_id is empty."""
    try:
        with _lock:
            con = _db()
            sql = (
                "SELECT mem_id, query_hash, recall_day, hits, total_score, first_at, last_at "
                "FROM recall_signals"
            )
            args: tuple = ()
            if mem_id:
                sql += " WHERE mem_id = ?"
                args = (mem_id,)
            cur = con.execute(sql + " ORDER BY last_at DESC", args)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        log.debug("recall signal read skipped: %s", e)
        return []


def demo() -> None:
    """Self-check: dedup inside a day, separate rows per distinct query wording."""
    global _conn
    _conn = connect(":memory:")
    record_recalls("what editor do I use", [("m1", 0.9)])
    record_recalls("what   EDITOR do I use ", [("m1", 0.7)])  # same wording once normalized
    record_recalls("where do I live", [("m1", 0.5), ("m2", 0.4)])

    rows = signals_for("m1")
    assert len(rows) == 2, f"expected 2 distinct query wordings for m1, got {len(rows)}"
    merged = next(r for r in rows if r["hits"] == 2)
    assert abs(merged["total_score"] - 1.6) < 1e-9, merged
    assert len(signals_for("m2")) == 1
    assert len(signals_for()) == 3
    assert query_hash("A  b") == query_hash("a b")
    print("ok")


if __name__ == "__main__":
    demo()
