"""recall.py — briefings/memory embedding into LanceDB + semantic recall
roundtrip. Uses the StubEmbedder (no HF download), temp dirs only."""
import sqlite3
from pathlib import Path

from base import TrackerTestCase
from base import StubEmbedder
import memory as mem
import recall as rc


class TestRecallIndex(TrackerTestCase):
    def setUp(self):
        super().setUp()
        self.out = Path(self.tmp) / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        rc.OUT_DIR = self.out
        rc.DB_PATH = Path(self.tmp_path("tracker.db"))
        rc.LANCEDB_DIR = Path(self.tmp) / "lancedb"
        rc.get_embedder = lambda: StubEmbedder()

        # one fake briefing
        (self.out / "briefing-2026-09-01-TEST.md").write_text(
            "# Briefing\n\nDeepSeek released a new sparse model this week.\n"
        )
        # one memory fact, via the real memory store schema
        mem.DB_PATH = Path(self.tmp_path("tracker.db"))
        conn = mem.connect()
        mem.remember(conn, "Modal releases a new frontier model", category="ai", importance=3)
        conn.close()

    def test_index_and_recall_roundtrip(self):
        nb, nm = rc.index_all(embedder=StubEmbedder())
        assert nb >= 1
        assert nm == 1

        res = rc.recall("frontier models", k=5, embedder=StubEmbedder())
        assert "articles" in res
        assert len(res["briefings"]) >= 1
        assert len(res["memories"]) >= 1

    def test_context_block_built(self):
        nb, nm = rc.index_all(embedder=StubEmbedder())
        block = rc.context_block("AI model release", k=5, embedder=StubEmbedder())
        assert "Prior knowledge recall" in block

    def test_index_is_idempotent_for_briefings(self):
        # second index with same files: no new briefing rows
        nb1, _ = rc.index_all(embedder=StubEmbedder())
        nb2, _ = rc.index_all(embedder=StubEmbedder())
        assert nb2 == 0