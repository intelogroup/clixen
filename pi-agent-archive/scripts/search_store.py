"""
PI-quantum science/tech/AI news tracker - search + fetch + store tier.

Search across Exa / Tavily / Brave (fallback order), fetch full article
content (Firecrawl preferred, fallback direct http), write to SQLite with
FTS5. Pure stdlib + requests. No framework.

Run standalone:
    python3 search_store.py --query "latest AI model release" --limit 8
    python3 search_store.py --seed-all        # run the default seed queries
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "tracker.db"
LANCEDB_DIR = ROOT / "data" / "lancedb"
OUT_DIR = ROOT / "out"

SEED_QUERIES = [
    ("ai", "latest artificial intelligence model release transformer research"),
    ("ai", "LLM benchmark state of the art breakthrough"),
    ("science", "major science discovery research paper breakthrough"),
    ("tech", "new technology hardware launch announcement"),
    ("science", "AI for drug discovery biotech genomics breakthrough"),
]

KNOWN_DOMAINS = {"exa": "exa", "tavily": "tavily", "brave": "brave"}


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"')


# ---------------- search backends ----------------

def _brave_search(query: str, count: int = 8) -> list[dict]:
    key = os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("BRAVE_API_KEY")
    if not key:
        return []
    headers = {"Accept": "application/json", "X-Subscription-Token": key}
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": count, "freshness": "pw"},
            headers=headers, timeout=10,
        )
        if r.status_code != 200:
            return []
        out = []
        for it in r.json().get("web", {}).get("results", [])[:count]:
            out.append({
                "title": it.get("title", ""),
                "url": it.get("url", ""),
                "snippet": (it.get("description", "") or "").replace("<strong>", "").replace("</strong>", ""),
                "published": (it.get("age", "") or ""),
                "source": "brave",
            })
        return out
    except Exception:
        return []


def _tavily_search(query: str, max_results: int = 8) -> list[dict]:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return []
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": max_results,
                  "search_depth": "basic", "include_answer": True, "topic": "news"},
            timeout=12,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        out = []
        ans = (data.get("answer") or "").strip()
        if ans:
            out.append({"title": "Tavily synthesized answer", "url": "", "snippet": ans,
                        "published": "", "source": "tavily"})
        for it in data.get("results", [])[:max_results]:
            out.append({
                "title": it.get("title", ""),
                "url": it.get("url", ""),
                "snippet": it.get("content", "") or "",
                "published": (it.get("published_date", "") or "")[:10],
                "source": "tavily",
            })
        return out
    except Exception:
        return []


def _exa_search(query: str, num_results: int = 8) -> list[dict]:
    key = os.environ.get("EXA_API_KEY")
    if not key:
        return []
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        r = requests.post(
            "https://api.exa.ai/search",
            json={"query": query, "numResults": num_results, "type": "auto",
                  "contents": {"text": {"maxCharacters": 1000}},
                  "text": {"maxCharacters": 1000}},
            headers=headers, timeout=15,
        )
        if r.status_code != 200:
            return []
        out = []
        for res in r.json().get("results", [])[:num_results]:
            out.append({
                "title": res.get("title", ""),
                "url": res.get("url", ""),
                "snippet": (res.get("text", "") or "")[:1000],
                "published": (res.get("publishedDate", "") or "")[:10],
                "source": "exa",
            })
        return out
    except Exception:
        return []


def search_web(query: str, limit: int = 8) -> list[dict]:
    """Search across backends, first to return items wins."""
    for fn in (_exa_search, _tavily_search, _brave_search):
        try:
            items = fn(query, limit)
            if items:
                return items
        except Exception:
            continue
    return []


# ---------------- fetch content ----------------

def fetch_page(url: str) -> str:
    """Fetch a URL, return markdown-ish text. Firecrawl preferred, then direct http."""
    fc = os.environ.get("FIRECRAWL_API_KEY")
    if fc:
        try:
            r = requests.post(
                "https://api.firecrawl.dev/v1/scrape",
                json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
                headers={"Authorization": f"Bearer {fc}"}, timeout=30,
            )
            if r.status_code == 200:
                data = r.json().get("data", {}) or {}
                md = data.get("markdown") or data.get("content") or ""
                if md and len(md) > 200:
                    return md
        except Exception:
            pass
    # direct http fallback
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"},
                         timeout=15)
        if r.status_code == 200:
            import html as _html
            text = _html.unescape(re.sub(r"<[^>]+>", " ", r.text))
            text = re.sub(r"\s+", " ", text).strip()
            return text[:4000]
    except Exception:
        pass
    return ""


def fetch_and_store(items: list[dict]) -> list[dict]:
    """Fetch full content for each search item and store to DB. Returns stored rows."""
    stored = []
    for it in items:
        url = (it.get("url") or "").strip()
        if not url:
            continue
        body = fetch_page(url) or it.get("snippet", "")
        row = {
            "id": str(uuid.uuid4()),
            "title": it.get("title", ""),
            "url": url,
            "snippet": it.get("snippet", ""),
            "body": body,
            "source": it.get("source", ""),
            "published": it.get("published", ""),
            "category": it.get("category", "general"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        stored.append(row)
    return stored


# ---------------- SQLite store with FTS5 ----------------

def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS articles (
        id TEXT PRIMARY KEY,
        title TEXT,
        url TEXT UNIQUE,
        content_hash TEXT UNIQUE,
        snippet TEXT,
        body TEXT,
        source TEXT,
        published TEXT,
        category TEXT,
        created_at TEXT
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
        title, body, content='articles', content_rowid='rowid'
    );
    CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
        INSERT INTO articles_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
    END;
    CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
        INSERT INTO articles_fts(articles_fts, rowid, title, body)
        VALUES ('delete', old.rowid, old.title, old.body);
    END;
    CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
        INSERT INTO articles_fts(articles_fts, rowid, title, body)
        VALUES ('delete', old.rowid, old.title, old.body);
        INSERT INTO articles_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
    END;
    """)
    # content_hash dedup for syndicated copies (same story, different URL);
    # backfill for pre-existing rows
    cols = [r[1] for r in conn.execute("PRAGMA table_info(articles)")]
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN content_hash TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_chash ON articles(content_hash)")
    conn.commit()
    _backfill_hashes(conn)
    return conn


def content_hash(title: str, body: str = "") -> str:
    # normalize: lowercase, collapse whitespace, strip punctuation/brackets so
    # syndicated copies of the same story hash identically
    import hashlib
    norm = re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (title or "").lower())).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:24]


def _backfill_hashes(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id, title, content_hash FROM articles WHERE content_hash IS NULL").fetchall()
    if not rows:
        return
    seen: dict[str, str] = {}
    for aid, title, ch in rows:
        h = content_hash(title)
        if h in seen:
            # duplicate of an already-hashed article; keep one, drop the copy
            conn.execute("DELETE FROM articles WHERE id=?", (aid,))
            try:
                conn.execute("DELETE FROM embedded WHERE article_id=?", (aid,))
            except sqlite3.OperationalError:
                pass  # ledger not created yet (embed_index owns it)
        else:
            seen[h] = aid
            conn.execute("UPDATE articles SET content_hash=? WHERE id=?", (h, aid))
    conn.commit()


def insert_articles(conn: sqlite3.Connection, rows: list[dict]) -> int:
    added = 0
    for r in rows:
        if not r.get("content_hash"):
            r = {**r, "content_hash": content_hash(r.get("title", ""), r.get("body", ""))}
        try:
            conn.execute(
                "INSERT OR IGNORE INTO articles (id,title,url,content_hash,snippet,body,source,published,category,created_at) "
                "VALUES (:id,:title,:url,:content_hash,:snippet,:body,:source,:published,:category,:created_at)", r)
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                added += 1
        except Exception:
            continue
    conn.commit()
    return added


def fts_search(conn: sqlite3.Connection, q: str, limit: int = 10) -> list[dict]:
    try:
        cur = conn.execute(
            "SELECT a.id,a.title,a.url,a.snippet,a.published,a.source,a.category,a.created_at "
            "FROM articles a JOIN articles_fts ON articles_fts.rowid = a.rowid "
            "WHERE articles_fts MATCH ? "
            "ORDER BY rank LIMIT ?", (q, limit))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []


def run_seed() -> None:
    load_env()
    conn = init_db()
    for category, query in SEED_QUERIES:
        print(f"[seed] searching: {query}")
        items = search_web(query)
        for it in items:
            it["category"] = category
        rows = fetch_and_store(items)
        n = insert_articles(conn, rows)
        print(f"  stored {n} new / {len(rows)} total from query")
        time.sleep(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", type=str, default=None)
    ap.add_argument("--category", type=str, default="general")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--seed-all", action="store_true")
    ap.add_argument("--fts", type=str, default=None)
    args = ap.parse_args()
    load_env()
    conn = init_db()
    if args.seed_all:
        run_seed()
        return
    if args.fts:
        for r in fts_search(conn, args.fts):
            print(f"- {r['title']} ({r['url']}) [{r['source']}]")
        return
    if args.query:
        items = search_web(args.query, args.limit)
        for it in items:
            it["category"] = args.category
        rows = fetch_and_store(items)
        n = insert_articles(conn, rows)
        print(f"[done] stored {n} new articles")
        return


if __name__ == "__main__":
    main()
