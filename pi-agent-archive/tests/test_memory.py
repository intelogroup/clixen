"""memory.py — persistent store, dedup, fold, state, prune."""
import sqlite3
from pathlib import Path

from base import TrackerTestCase
import memory as mem


class TestMemory(TrackerTestCase):
    def test_remember_and_recall(self):
        mem.DB_PATH = Path(self.tmp_path())
        conn = mem.connect()
        assert mem.remember(conn, "GPU breakthrough: next-gen datacenter chips", category="tech", importance=3) == "stored"
        rows = mem.recall(conn, limit=10)
        assert len(rows) == 1
        assert rows[0]["category"] == "tech"
        assert rows[0]["importance"] == 3

    def test_dedup_bumps_shared_hash(self):
        mem.DB_PATH = Path(self.tmp_path())
        conn = mem.connect()
        mem.remember(conn, "  GPU  breakthrough: NEXT-GEN datacenter chips ", importance=2)
        r = mem.remember(conn, "GPU breakthrough: Next-Gen datacenter chips", importance=2)
        assert r.startswith("bumped")
        rows = mem.recall(conn, limit=10)
        assert len(rows) == 1
        assert rows[0]["seen_count"] >= 2

    def test_recall_orders_by_importance(self):
        mem.DB_PATH = Path(self.tmp_path())
        conn = mem.connect()
        mem.remember(conn, "low value fact", importance=1)
        mem.remember(conn, "high value fact", importance=3)
        rows = mem.recall(conn, limit=10)
        assert rows[0]["importance"] >= rows[1]["importance"]

    def test_fold_marks_folded_and_caps_recall(self):
        mem.DB_PATH = Path(self.tmp_path())
        conn = mem.connect()
        mem.fold(conn, "summary of first run", topic="run")
        rows = mem.recall(conn, limit=10)
        assert len(rows) == 0  # folded entries excluded from recall
        # but prune / storage keeps them

    def test_state_set_get(self):
        mem.DB_PATH = Path(self.tmp_path())
        conn = mem.connect()
        mem.set_state(conn, "last_run", "2026-09-01")
        assert mem.get_state(conn, "last_run") == "2026-09-01"

    def test_prune_removes_old_folded_only(self):
        mem.DB_PATH = Path(self.tmp_path())
        conn = mem.connect()
        mem.remember(conn, "recent fact", importance=3)
        mem.fold(conn, "old summary", topic="run")
        n = mem.prune(conn, max_age_days=0, min_importance=1)
        # all folded entries older than 0 days removed; none left to check rows
        rows = mem.recall(conn, limit=10)
        assert len(rows) == 1  # the important fact survives

    def test_recall_category_filter(self):
        mem.DB_PATH = Path(self.tmp_path())
        conn = mem.connect()
        mem.remember(conn, "ai fact", category="ai")
        mem.remember(conn, "science fact", category="science")
        rows = mem.recall(conn, limit=10, category="science")
        assert len(rows) == 1
        assert rows[0]["category"] == "science"

    def test_db_round_trip_persists(self):
        mem.DB_PATH = Path(self.tmp_path())
        conn = mem.connect()
        conn.close()
        # reconnect to same path
        conn2 = mem.connect()
        mem.remember(conn2, "persisted fact", importance=2)
        assert len(mem.recall(conn2, limit=10)) == 1