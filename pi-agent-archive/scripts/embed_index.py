"""
PI-quantum tracker - embeddings + LanceDB vector store tier.

Chunks article bodies, embeds with sentence-transformers, writes to LanceDB.
Supports nearest-neighbor semantic search across the corpus.

Run standalone:
    python3 embed_index.py            # index all articles not yet embedded
    python3 embed_index.py --query "vector search question"   # semantic search
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "tracker.db"
LANCEDB_DIR = ROOT / "data" / "lancedb"

MODEL_NAME = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
BATCH = 64


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


def get_embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME)


def chunk_text(text: str, max_chars: int = 800, overlap: int = 80) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            cut = text.rfind(" ", start, end)
            if cut > start + max_chars // 2:
                end = cut
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break  # tail reached; don't emit overlap-driven suffix chunks
        newstart = end - overlap
        if newstart <= start:
            newstart = start + 1  # safety: guarantee forward progress
        start = newstart
    return chunks


def init_lance(table_name: str = "articles"):
    import lancedb
    LANCEDB_DIR.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(LANCEDB_DIR))
    _names = db.table_names()
    return db, db.open_table(table_name) if table_name in _names else None


def to_table(db, table_name, data, schema):
    if not data:
        return
    return db.create_table(table_name, data, schema=schema, mode="overwrite",
                           embed=None)


def init_ledger(conn: sqlite3.Connection) -> None:
    # Tracks which articles are already embedded. (LanceDB read-back for this
    # purpose needs pandas/pylance + pyarrow kwargs that aren't available, so
    # SQLite is the source of truth for idempotency.)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS embedded (
        article_id TEXT PRIMARY KEY,
        n_chunks INTEGER,
        indexed_at TEXT
    )
    """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(embedded)")]
    if "dup_of" not in cols:
        conn.execute("ALTER TABLE embedded ADD COLUMN dup_of TEXT")


NEARDUP_THRESHOLD = float(os.environ.get("NEARDUP_THRESHOLD", "0.92"))


def _title_vec(embedder, title: str) -> list:
    return embedder.encode([title], normalize_embeddings=True)[0].tolist()


def _ensure_titles_table(db, embedder, conn) -> None:
    """Backfill title vectors for already-embedded articles (one-time)."""
    import pyarrow as pa
    if "titles" in db.table_names():
        return
    rows = conn.execute(
        "SELECT a.id, a.title FROM articles a JOIN embedded e ON e.article_id = a.id "
        "WHERE e.dup_of IS NULL").fetchall()
    schema = pa.schema([
        pa.field("article_id", pa.string()),
        pa.field("title", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), 384)),
    ])
    records = [{"article_id": aid, "title": t or "",
                "vector": _title_vec(embedder, t or "")} for aid, t in rows]
    if records:
        db.create_table("titles", records, schema=schema, mode="overwrite")


def _near_dup_of(db, embedder, title: str) -> str | None:
    """Return the article_id of a near-duplicate title, else None."""
    import pyarrow as pa  # noqa: F401  (search returns pyarrow-backed list)
    if "titles" not in db.table_names():
        return None
    try:
        rs = (db.open_table("titles").search(_title_vec(embedder, title))
              .metric("cosine").limit(1)
              .select(["article_id", "title"]).to_list())
    except Exception:
        return None
    if not rs:
        return None
    dist = rs[0].get("_distance")
    if dist is None:
        return None
    sim = 1.0 - float(dist)
    if sim >= NEARDUP_THRESHOLD:
        return rs[0]["article_id"]
    return None


def index_all(model=None, embedder=None) -> int:
    import lancedb
    import pyarrow as pa

    load_env()
    conn = sqlite3.connect(DB_PATH)
    init_ledger(conn)
    rows = conn.execute("SELECT id,title,body,url,category FROM articles").fetchall()

    if embedder is None:
        embedder = get_embedder()

    db = lancedb.connect(str(LANCEDB_DIR))
    table_name = "articles"

    seen = conn.execute(
        "SELECT article_id FROM embedded WHERE article_id IN "
        "(SELECT id FROM articles)").fetchall()
    existing_ids = {r[0] for r in seen}

    schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("title", pa.string()),
        pa.field("url", pa.string()),
        pa.field("category", pa.string()),
        pa.field("chunk_idx", pa.int32()),
        pa.field("text", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), 384)),
    ])

    to_embed = []
    skipped_dups: list[tuple[str, str]] = []
    _ensure_titles_table(db, embedder, conn)
    for a in rows:
        mid, title, body, url, cat = a
        if mid in existing_ids:
            continue
        dup_of = _near_dup_of(db, embedder, title or "")
        if dup_of:
            skipped_dups.append((mid, dup_of))
            continue
        chunks = chunk_text(f"{title}. {body}")
        for ci, c in enumerate(chunks):
            to_embed.append({"id": mid, "title": title, "url": url or "", "category": cat or "",
                             "chunk_idx": ci, "text": c})

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for aid, dup in skipped_dups:
        print(f"[embed] near-dup skipped: {aid} ~ {dup}")
        conn.execute(
            "INSERT INTO embedded(article_id, n_chunks, indexed_at, dup_of) VALUES(?,0,?,?) "
            "ON CONFLICT(article_id) DO UPDATE SET dup_of=excluded.dup_of",
            (aid, now, dup))
    conn.commit()
    if skipped_dups and not to_embed:
        print(f"[embed] no new content to index ({len(skipped_dups)} near-dups skipped)")
        conn.close()
        return 0
    if not to_embed:
        print("[embed] no new content to index")
        conn.close()
        return 0

    added = 0
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    done_ids: dict[str, int] = {}
    for i in range(0, len(to_embed), BATCH):
        batch = to_embed[i:i + BATCH]
        emb = embedder.encode([b["text"] for b in batch], normalize_embeddings=True)
        records = []
        for b, vec in zip(batch, emb):
            records.append({**b, "vector": vec.tolist()})
        _names = db.table_names()
        if table_name in _names:
            db.open_table(table_name).add(records)
        else:
            db.create_table(table_name, records, schema=schema, mode="overwrite")
        added += len(records)
        for b in batch:
            done_ids[b["id"]] = done_ids.get(b["id"], 0) + 1
        print(f"[embed] indexed {added}/{len(to_embed)} chunks")

    # mark articles fully embedded (batch-level fine; idempotent via PK upsert)
    for aid, nch in done_ids.items():
        conn.execute(
            "INSERT INTO embedded(article_id, n_chunks, indexed_at) VALUES(?,?,?) "
            "ON CONFLICT(article_id) DO UPDATE SET n_chunks=excluded.n_chunks, "
            "indexed_at=excluded.indexed_at", (aid, nch, now))
        # register title vector for future near-dup checks
        trow = next((r for r in rows if r[0] == aid), None)
        if trow:
            import pyarrow as pa2
            tschema = pa2.schema([
                pa2.field("article_id", pa2.string()),
                pa2.field("title", pa2.string()),
                pa2.field("vector", pa2.list_(pa2.float32(), 384)),
            ])
            trec = [{"article_id": aid, "title": trow[1] or "",
                     "vector": _title_vec(embedder, trow[1] or "")}]
            tnames = db.table_names()
            if "titles" in tnames:
                db.open_table("titles").add(trec)
            else:
                db.create_table("titles", trec, schema=tschema, mode="overwrite")
    conn.commit()
    conn.close()
    return added


def semantic_search(query: str, k: int = 5, embedder=None):
    import lancedb
    load_env()
    db = lancedb.connect(str(LANCEDB_DIR))
    table_name = "articles"
    if table_name not in db.table_names():
        return []
    if embedder is None:
        embedder = get_embedder()
    vec = embedder.encode([query], normalize_embeddings=True)[0].tolist()
    t = db.open_table(table_name)
    rs = (
        t.search(vec)
        .metric("cosine")
        .limit(k)
        .select(["title", "url", "category", "text"])
        .to_list()
    )
    return rs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", type=str, default=None)
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    if args.query:
        embedder = get_embedder()
        for r in semantic_search(args.query, args.k, embedder):
            print(f"[{r['category']}] {r['title']}\n  {r['url']}\n  {r['text'][:300]}\n")
        return

    t0 = time.time()
    n = index_all()
    print(f"[done] indexed {n} chunks in {time.time()-t0:.1f}s")


def _hard_exit(rc: int) -> None:
    # Flush pending output, then hard-exit to skip flaky LanceDB/pyarrow/torch
    # native finalizers that segfault (exit 139) at interpreter shutdown AFTER
    # all real work is done — cosmetic noise that can mask failures in cron logs.
    import os, gc, sys as _sys
    gc.collect()
    try:
        _sys.stdout.flush()
        _sys.stderr.flush()
    except Exception:
        pass
    os._exit(rc)


if __name__ == "__main__":
    rc = 0
    try:
        main()
    except SystemExit as e:
        rc = int(e.code or 0)
    except BaseException:
        import traceback
        traceback.print_exc()
        rc = 1
    _hard_exit(rc)
