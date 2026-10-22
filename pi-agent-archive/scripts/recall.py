"""
PI-quantum tracker - vector recall across past briefings, memory, and articles.

Embedds:
  - briefings (out/*.md)  -> LanceDB table "briefings"
  - memory facts (SQLite) -> LanceDB table "memories"
  - (articles already embedded by embed_index.py -> table "articles")

Provides semantic search across all three so the agent can recall what it already
knows before writing a fresh briefing. Reuses the same sentence-transformers model
as embed_index.py (cached on disk).

CLI:
  python3 recall.py index                     # (re)embed briefings + memory
  python3 recall.py "query" [--k 5]           # semantic recall across all
  python3 recall.py --articles "q"            # only articles
  python3 recall.py --context "q" [--k 6]     # folded agent context block
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "tracker.db"
LANCEDB_DIR = ROOT / "data" / "lancedb"
OUT_DIR = ROOT / "out"

MODEL_NAME = "all-MiniLM-L6-v2"
DIM = 384
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

import os  # noqa: E402


def get_embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME)


def _chunk(text: str, max_chars: int = 900) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    out = []
    for i in range(0, len(text), max_chars):
        out.append(text[i:i + max_chars])
    return out


def _table_names(db):
    try:
        return db.table_names()
    except Exception:
        return [t[0] for t in db.list_tables() if isinstance(t, tuple)]


def _has_table(db, name: str) -> bool:
    return name in _table_names(db)


def index_briefings(db, embedder) -> int:
    import pyarrow as pa
    files = sorted(OUT_DIR.glob("*.md")) if OUT_DIR.exists() else []
    if not files:
        return 0
    schema = pa.schema([
        pa.field("title", pa.string()),
        pa.field("path", pa.string()),
        pa.field("text", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), DIM)),
    ])
    # ledjer: SQLite is the source of truth for which briefings are embedded
    # (LanceDB read-back needs pandas/pylance which aren't installed).
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS indexed_briefings (
        path TEXT PRIMARY KEY,
        n_chunks INTEGER,
        indexed_at TEXT
    )""")
    seen_paths = {r[0] for r in conn.execute("SELECT path FROM indexed_briefings").fetchall()}
    new_files = [f for f in files if str(f) not in seen_paths]
    if not new_files:
        conn.close()
        return 0
    to_add = []
    for f in new_files:
        text = f.read_text(errors="ignore")
        for ci, c in enumerate(_chunk(text)):
            to_add.append({"title": f.name, "path": str(f),
                           "text": f"[{f.name}]\n{c}"})
    if not to_add:
        conn.close()
        return 0
    emb = embedder.encode([r["text"] for r in to_add], normalize_embeddings=True, batch_size=BATCH)
    records = [{**r, "vector": v.tolist()} for r, v in zip(to_add, emb)]
    if _has_table(db, "briefings"):
        db.open_table("briefings").add(records)
    else:
        db.create_table("briefings", records, schema=schema, mode="overwrite")
    import time
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for f in new_files:
        nchunks = len(_chunk(f.read_text(errors="ignore")))
        conn.execute(
            "INSERT OR IGNORE INTO indexed_briefings(path, n_chunks, indexed_at) VALUES(?,?,?)",
            (str(f), nchunks, now))
    conn.commit()
    conn.close()
    return len(records)


def index_memory(db, embedder) -> int:
    import pyarrow as pa
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT text,category,importance FROM memory WHERE folded=0 AND importance>=1"
    ).fetchall()
    if not rows:
        return 0
    schema = pa.schema([
        pa.field("text", pa.string()),
        pa.field("category", pa.string()),
        pa.field("importance", pa.int32()),
        pa.field("vector", pa.list_(pa.float32(), DIM)),
    ])
    texts = [f"[{r['category']}] {r['text']}" for r in rows]
    emb = embedder.encode(texts, normalize_embeddings=True, batch_size=BATCH)
    records = [
        {"text": texts[i], "category": rows[i]['category'],
         "importance": rows[i]['importance'], "vector": emb[i].tolist()}
        for i in range(len(rows))
    ]
    if _has_table(db, "memories"):
        # for simplicity, rebuild memory vectors each run (memory is small)
        db.drop_table("memories")
    db.create_table("memories", records, schema=schema, mode="overwrite")
    return len(records)


def index_all(embedder=None) -> tuple[int, int]:
    import lancedb
    db = lancedb.connect(str(LANCEDB_DIR))
    if embedder is None:
        embedder = get_embedder()
    nb = index_briefings(db, embedder)
    nm = index_memory(db, embedder)
    return nb, nm


def _search_table(db, table: str, vec: list, k: int, cols: list[str]):
    if not _has_table(db, table):
        return []
    try:
        rs = (db.open_table(table).search(vec).metric("cosine").limit(k)
              .select(cols).to_list())
        return rs
    except Exception:
        return []


def recall(query: str, k: int = 5, embedder=None) -> dict:
    import lancedb
    db = lancedb.connect(str(LANCEDB_DIR))
    if embedder is None:
        embedder = get_embedder()
    vec = embedder.encode([query], normalize_embeddings=True)[0].tolist()
    out = {
        "articles": _search_table(db, "articles", vec, k, ["title", "url", "category", "text"]),
        "briefings": _search_table(db, "briefings", vec, k, ["title", "path", "text"]),
        "memories": _search_table(db, "memories", vec, k, ["text", "category", "importance"]),
    }
    return out


def context_block(query: str, k: int = 6, embedder=None) -> str:
    """Return a folded, model-ready context string for the agent system prompt."""
    res = recall(query, k, embedder)
    lines = ["## Prior knowledge recall (semantic)"]
    arts = res.get("articles") or []
    brefs = res.get("briefings") or []
    mems = res.get("memories") or []
    if not (arts or brefs or mems):
        return "## Prior knowledge recall (semantic)\n(none yet)"
    if arts:
        lines.append("Recent relevant articles:")
        for a in arts[:3]:
            lines.append(f"- {a['title']} ({a.get('url') or 'no url'})")
    if mems:
        lines.append("Memory facts:")
        for m in mems[:4]:
            lines.append(f"- [{m['category']}] {m['text']}")
    if brefs:
        lines.append("Last related briefing excerpt:")
        for b in brefs[:2]:
            excerpt = re.sub(r"\s+", " ", b['text'])
            lines.append(f"- {excerpt[:400]}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--articles", action="store_true")
    ap.add_argument("--context", action="store_true")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("query", nargs="?", default=None)
    args = ap.parse_args()

    if args.index:
        nb, nm = index_all()
        print(f"[recall] indexed briefings={nb} memories={nm}")
        return

    if not args.query:
        print("provide a query or --index")
        raise SystemExit(1)

    embedder = get_embedder()
    if args.context:
        print(context_block(args.query, args.k, embedder))
    else:
        res = recall(args.query, args.k, embedder)
        if args.articles:
            for a in res["articles"]:
                print(f"[{a['category']}] {a['title']}\n  {a['url']}\n")
        else:
            for k_, v in res.items():
                print(f"=== {k_} ({len(v)}) ===")
                for i, r in enumerate(v[:args.k]):
                    txt = re.sub(r"\s+", " ", r.get("text", ""))[:200]
                    print(f"  {i+1}. {txt}")


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
