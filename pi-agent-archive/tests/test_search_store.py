"""search_store.py — DB schema/FTS layer. Network calls are NOT made here:
only the SQLite schema, insert, and FTS search paths."""
import sqlite3
from pathlib import Path

from base import TrackerTestCase
import search_store as ss


class TestSchema(TrackerTestCase):
    def test_init_db_creates_schema(self):
        ss.DB_PATH = Path(self.tmp_path())
        conn = ss.init_db()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "articles" in tables
        assert "articles_fts" in tables
        trigs = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert "articles_ai" in trigs
        assert "articles_ad" in trigs
        assert "articles_au" in trigs  # update re-sync trigger

    def test_insert_and_fts_roundtrip(self):
        ss.DB_PATH = Path(self.tmp_path())
        conn = ss.init_db()
        row = {
            "id": "u1", "title": "Neural nets reach parity", "url": "https://x.com/1",
            "snippet": "s", "body": "Researchers reported neural network performance parity last week.",
            "source": "tavily", "published": "2026-08-30", "category": "ai",
            "created_at": "2026-09-01T00:00:00Z",
        }
        n = ss.insert_articles(conn, [row])
        assert n == 1
        hits = ss.fts_search(conn, "neural")
        assert len(hits) == 1
        assert hits[0]["id"] == "u1"

    def test_fts_insert_or_ignore_dedup(self):
        ss.DB_PATH = Path(self.tmp_path())
        conn = ss.init_db()
        row = {
            "id": "u2", "title": "t", "url": "https://x.com/dup", "snippet": "",
            "body": "body text here with some words", "source": "exa", "published": "",
            "category": "ai", "created_at": "2026-09-01T00:00:00Z",
        }
        n1 = ss.insert_articles(conn, [row])
        row2 = dict(row)
        row2["id"] = "u3"
        n2 = ss.insert_articles(conn, [row2])  # same url -> ignored
        assert (n1, n2) == (1, 0)

    def test_fts_bad_query_returns_empty(self):
        ss.DB_PATH = Path(self.tmp_path())
        conn = ss.init_db()
        assert ss.fts_search(conn, "!!!bad-syntax!!!") == []

    def test_content_hash_dedups_syndicated_copies(self):
        # same story from two outlets (different url) -> only first stored
        ss.DB_PATH = Path(self.tmp_path())
        conn = ss.init_db()
        base = {"title": "Apple's New CEO Takes Over", "snippet": "", "body": "b",
                "source": "exa", "published": "", "category": "tech",
                "created_at": "2026-09-01T00:00:00Z"}
        r1 = {**base, "id": "s1", "url": "https://a.com/story"}
        r2 = {**base, "id": "s2", "url": "https://b.com/syndicated"}
        n1 = ss.insert_articles(conn, [r1])
        n2 = ss.insert_articles(conn, [r2])
        assert (n1, n2) == (1, 0)
        assert conn.execute("SELECT count(*) FROM articles").fetchone()[0] == 1

    def test_content_hash_ignores_case_and_punctuation(self):
        ss.DB_PATH = Path(self.tmp_path())
        conn = ss.init_db()
        base = {"snippet": "", "body": "b", "source": "exa", "published": "",
                "category": "ai", "created_at": "2026-09-01T00:00:00Z"}
        r1 = {**base, "id": "h1", "url": "https://a.com/1",
              "title": "NVIDIA launches Cosmos 3!"}
        r2 = {**base, "id": "h2", "url": "https://b.com/2",
              "title": "nvidia  launches   cosmos 3"}
        ss.insert_articles(conn, [r1])
        n2 = ss.insert_articles(conn, [r2])
        assert n2 == 0

    def test_distinct_titles_both_stored(self):
        ss.DB_PATH = Path(self.tmp_path())
        conn = ss.init_db()
        base = {"snippet": "", "body": "b", "source": "exa", "published": "",
                "category": "ai", "created_at": "2026-09-01T00:00:00Z"}
        r1 = {**base, "id": "d1", "url": "https://a.com/1", "title": "Story one"}
        r2 = {**base, "id": "d2", "url": "https://b.com/2", "title": "Story two"}
        n = ss.insert_articles(conn, [r1, r2])
        assert n == 2

    def test_backfill_hashes_removes_existing_dups(self):
        # pre-hash-era rows with same normalized title get collapsed
        ss.DB_PATH = Path(self.tmp_path())
        conn = ss.init_db()
        conn.execute("INSERT INTO articles (id,title,url,body,category,created_at) "
                     "VALUES ('p1','Same Story Here','https://a.com','x','ai','2026-01-01')")
        conn.execute("INSERT INTO articles (id,title,url,body,category,created_at) "
                     "VALUES ('p2','same  story! here','https://b.com','y','ai','2026-01-01')")
        conn.commit()
        conn.close()
        conn2 = ss.init_db()  # re-init triggers backfill
        n = conn2.execute("SELECT count(*) FROM articles").fetchone()[0]
        assert n == 1

    def test_fetch_and_store_builds_rows(self):
        # pure builder, no network
        items = [{"title": "t", "url": "https://x.com/a", "snippet": "s",
                  "source": "exa", "published": "2026-08-31", "category": "science"}]
        rows = ss.fetch_and_store(items)
        assert len(rows) == 1
        assert rows[0]["url"] == "https://x.com/a"
        assert rows[0]["category"] == "science"