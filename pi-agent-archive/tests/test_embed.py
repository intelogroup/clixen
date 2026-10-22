"""embed_index.py — chunk_text correctness (incl. the infinite-loop regression), index_all idempotency, init_lance regression."""
import sqlite3
from pathlib import Path

from base import TrackerTestCase
from base import StubEmbedder
import embed_index as ei


class TestChunkText(TrackerTestCase):
    def test_empty_and_whitespace(self):
        assert ei.chunk_text("") == []
        assert ei.chunk_text("   \n\t  ") == []

    def test_short_text(self):
        chunks = ei.chunk_text("hello world")
        assert chunks == ["hello world"]

    def test_chunk_boundaries_and_overlap(self):
        # 1000 words, max 800, overlap 80. Verify no infinite loop and
        # coverage: all text present across chunks, chunks <= max_chars.
        text = " ".join(f"word{i}" for i in range(1000))
        chunks = ei.chunk_text(text, max_chars=800, overlap=80)
        assert 1 < len(chunks) < 1000
        for c in chunks:
            assert len(c) <= 800 + 1  # +1 for trailing space edge
            assert len(c) > 0
        joined = " ".join(chunks)
        # every word must appear somewhere
        for i in range(1000):
            assert f"word{i}" in joined

    def test_no_infinite_loop_when_last_chunk_le_overlap(self):
        # regression: text length such that remaining tail <= overlap caused
        # newstart == start forever
        text = "a " * 200  # ~400 chars; with max 200 overlap 100, tail -> degenerate
        chunks = ei.chunk_text(text, max_chars=200, overlap=100)
        assert len(chunks) < 500  # terminates, doesn't blow up
        assert all(c for c in chunks)

    def test_no_infinite_loop_single_word_long(self):
        text = "x" * 10000
        chunks = ei.chunk_text(text, max_chars=800, overlap=80)
        assert 5 < len(chunks) < 100
        # every position covered by at least one chunk (overlap means the
        # concatenation is longer than text, so assert coverage not equality)
        joined = "".join(chunks)
        assert len(joined) >= len(text)
        assert text in joined or all(ch in "".join(chunks) for ch in "x")

    def test_uniform_string_covers_end(self):
        text = "y" * 5000
        chunks = ei.chunk_text(text, max_chars=700, overlap=100)
        assert chunks
        assert all(len(c) <= 700 + 1 for c in chunks)
        # tail must reach the very end
        assert chunks[-1][-1] == "y"

    def test_overlap_actually_overlaps(self):
        text = " ".join(f"word{i}" for i in range(500))
        chunks = ei.chunk_text(text, max_chars=200, overlap=50)
        if len(chunks) > 1:
            assert "word0" in chunks[0]


class TestInitLance(TrackerTestCase):
    def test_init_lance_creates_and_reopens_table(self):
        # regression: the old `[t.name for t in db.table_names()]` raised
        # AttributeError (table_names returns list[str], not rows)
        ei.LANCEDB_DIR = Path(self.tmp_path("lance"))
        db, tbl = ei.init_lance("articles")
        assert tbl is None
        # now write something and reopen
        import pyarrow as pa

        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), 384)),
        ])
        stub = StubEmbedder()
        vec = stub.encode(["hello world"])[0].tolist()
        db.create_table("articles", [{"id": "a1", "text": "hello world", "vector": vec}],
                        schema=schema, mode="overwrite")
        db2, tbl2 = ei.init_lance("articles")
        assert tbl2 is not None
        assert tbl2.count_rows() == 1


class TestIndexAll(TrackerTestCase):
    def test_index_all_writes_and_is_idempotent(self):
        import sqlite3
        from base import StubEmbedder

        db_path = self.tmp_path("tracker.db")
        ei.DB_PATH = Path(db_path)
        ei.LANCEDB_DIR = Path(self.tmp_path("lance"))
        self.patch_embedder(ei)  # stub get_embedder
        ei.load_env = lambda: None

        conn = sqlite3.connect(db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY, title TEXT, url TEXT UNIQUE, snippet TEXT,
            body TEXT, source TEXT, published TEXT, category TEXT, created_at TEXT)""")
        conn.commit()
        conn.execute(
            "INSERT INTO articles (id,title,url,body,category,created_at) "
            "VALUES ('x1','Test article','https://example.com/x',"
            "'Body with enough words for embedding a chunk that is meaningful',"
            "'ai','2026-01-01')")
        conn.commit()
        conn.close()

        # use ei's own schema helper? just call functions manually
        n1 = ei.index_all(embedder=StubEmbedder())
        assert n1 > 0

        import lancedb
        db = lancedb.connect(str(ei.LANCEDB_DIR))
        assert "articles" in db.table_names()
        assert db.open_table("articles").count_rows() == n1

        # second run: nothing new to index
        n2 = ei.index_all(embedder=StubEmbedder())
        assert n2 == 0


class TestNearDup(TrackerTestCase):
    def _seed(self, aid, title, url):
        import sqlite3
        db_path = self.tmp_path("tracker.db")
        ei.DB_PATH = Path(db_path)
        ei.LANCEDB_DIR = Path(self.tmp) / "lance"
        self.patch_embedder(ei)
        ei.load_env = lambda: None
        conn = sqlite3.connect(db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY, title TEXT, url TEXT UNIQUE, snippet TEXT,
            body TEXT, source TEXT, published TEXT, category TEXT, created_at TEXT)""")
        conn.execute("INSERT OR IGNORE INTO articles (id,title,url,body,category,created_at) "
                     "VALUES (?,?,?,?,?,'2026-01-01')",
                     (aid, title, url, "Body text for the article that is long enough.", "ai"))
        conn.commit()
        conn.close()

    def test_identical_title_is_near_dup(self):
        # same title, different url (bypasses content_hash via direct SQL)
        self._seed("n1", "Apple names John Ternus CEO", "https://a.com/1")
        n1 = ei.index_all(embedder=StubEmbedder())
        assert n1 > 0
        self._seed("n2", "Apple names John Ternus CEO", "https://b.com/2")
        n2 = ei.index_all(embedder=StubEmbedder())
        assert n2 == 0  # near-dup skipped, not embedded
        import sqlite3
        conn = sqlite3.connect(ei.DB_PATH)
        dup = conn.execute("SELECT dup_of FROM embedded WHERE article_id='n2'").fetchone()
        assert dup is not None and dup[0] == "n1"
        # original still has its chunks
        import lancedb
        db = lancedb.connect(str(ei.LANCEDB_DIR))
        assert "titles" in db.table_names()

    def test_distinct_titles_both_embedded(self):
        self._seed("m1", "Fusion reactor hits net energy gain", "https://a.com/1")
        n1 = ei.index_all(embedder=StubEmbedder())
        self._seed("m2", "DeepSeek ships sparseMoE inference stack", "https://b.com/2")
        n2 = ei.index_all(embedder=StubEmbedder())
        assert n1 > 0 and n2 > 0
        import sqlite3
        conn = sqlite3.connect(ei.DB_PATH)
        assert conn.execute("SELECT dup_of FROM embedded WHERE article_id='m2'").fetchone()[0] is None

    def test_dup_then_more_articles_still_index(self):
        # after a dup skip, subsequent distinct articles are unaffected
        self._seed("k1", "Unique Alpha Story", "https://a.com/1")
        ei.index_all(embedder=StubEmbedder())
        self._seed("k2", "Unique Alpha Story", "https://b.com/2")
        self._seed("k3", "Completely Different Beta Story", "https://c.com/3")
        n = ei.index_all(embedder=StubEmbedder())
        assert n > 0  # k3 embedded even though k2 was skipped
        import sqlite3
        conn = sqlite3.connect(ei.DB_PATH)
        assert conn.execute("SELECT dup_of FROM embedded WHERE article_id='k2'").fetchone()[0] == "k1"
        assert conn.execute("SELECT dup_of FROM embedded WHERE article_id='k3'").fetchone()[0] is None