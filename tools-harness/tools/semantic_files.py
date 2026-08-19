"""
Semantic file search — embeds file contents with nomic-embed-text and stores
them in LanceDB so the model can search by meaning rather than exact pattern.

Two tools:
  index_directory    — crawl a directory and embed all text files into LanceDB
  semantic_file_search — search indexed files by meaning (vector similarity)
"""

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path

import lancedb
import ollama
import pyarrow as pa

_OFFICE_EXTS = {".pdf", ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt", ".csv"}

_HOME = str(Path.home())
# 2026-08-04: file index moves to the OS app-private data home (not ~/.cache,
# not repo data/, never ~/Documents) — see THREAT_MODEL.md.
def _resolve_db_path() -> str:
    import os as _os

    override = _os.environ.get("CLIXEN_FILE_INDEX_PATH", "").strip()
    if override:
        return override
    from tools.vault_paths import db_path as _vault_db_path, ensure_data_dir, migrate_legacy_data

    ensure_data_dir()
    migrate_legacy_data()
    return _vault_db_path("file_index.lance")


_DB_PATH = None
_TABLE = "file_chunks"
_EMBED_MODEL = "nomic-embed-text"
_EMBED_DIM = 768
_CHUNK_CHARS = 1500   # chars per chunk (~375 tokens)
_CHUNK_OVERLAP = 200  # overlap between chunks

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
              ".cache", "site-packages", "dist", "build"}
_SKIP_EXTS = {".pyc", ".pyo", ".so", ".dylib", ".bin", ".exe",
              ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".mp4",
              ".wav", ".ogg", ".zip", ".tar", ".gz",
              ".onnx", ".gguf", ".safetensors", ".pt", ".pth"}

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

INDEX_DIR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "index_directory",
        "description": (
            "Index a directory's files into a semantic vector database so they "
            "can be searched by meaning. Run this once on a folder before using "
            "semantic_file_search on it. Re-running skips already-indexed files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to index (e.g. ~/Developer/myproject)",
                },
                "glob": {
                    "type": "string",
                    "description": "File pattern to include (default: all text files)",
                    "default": "*",
                },
                "refresh": {
                    "type": "boolean",
                    "description": "Replace existing vectors for matching files.",
                    "default": False,
                },
            },
            "required": ["path"],
        },
    },
}

SEMANTIC_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "semantic_file_search",
        "description": (
            "FIRST TOOL to call for any open-ended question about file contents — 'is "
            "there a note about X', 'find where Y is mentioned', 'what did we say about "
            "Z'. Finds relevant code, docs, or text by meaning even if the exact words "
            "don't match. Do NOT use list_directory or grep_files to hunt for this kind "
            "of answer — call this first. For exact identifiers, error strings, names, "
            "or quoted phrases, use fulltext_search instead — it's faster and more "
            "precise for literal matches. Requires the directory to be indexed first "
            "with index_directory (call that first if this returns no index found)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you're looking for, in plain language",
                },
                "path_filter": {
                    "type": "string",
                    "description": "Optional: restrict results to files under this path",
                    "default": "",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of relevant chunks to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

# ---------------------------------------------------------------------------
# LanceDB helpers
# ---------------------------------------------------------------------------

_SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("path", pa.string()),
    pa.field("chunk_index", pa.int32()),
    pa.field("text", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), _EMBED_DIM)),
])


def _get_table():
    db = lancedb.connect(_resolve_db_path())
    if _TABLE in db.list_tables().tables:
        return db.open_table(_TABLE)
    return db.create_table(_TABLE, schema=_SCHEMA)


def _embed(text: str) -> list:
    resp = ollama.embeddings(model=_EMBED_MODEL, prompt=text)
    return resp["embedding"]


def _chunk(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + _CHUNK_CHARS
        chunks.append(text[start:end])
        start += _CHUNK_CHARS - _CHUNK_OVERLAP
    return chunks


def _file_id(path: str, chunk_index: int) -> str:
    h = hashlib.md5(path.encode()).hexdigest()[:8]
    return f"{h}_{chunk_index}"


def _extract_text(fp: Path) -> str:
    """Extract readable text. PDF/Excel/Word go through their real converters
    (raw-byte utf-8 decoding produces garbage for these binary formats)."""
    suffix = fp.suffix.lower()
    if suffix not in _OFFICE_EXTS:
        return fp.read_bytes()[:80_000].decode("utf-8", errors="replace")

    tmp_dir = tempfile.mkdtemp(prefix="semidx_")
    try:
        if suffix == ".pdf":
            from tools.pdf_tools import pdf_to_markdown
            _, content = pdf_to_markdown(str(fp), tmp_dir)
        elif suffix in (".xlsx", ".xls"):
            from tools.office_tools import excel_to_markdown
            _, content = excel_to_markdown(str(fp), tmp_dir)
        elif suffix in (".pptx", ".ppt"):
            from tools.office_tools import pptx_to_markdown
            _, content = pptx_to_markdown(str(fp), tmp_dir)
        elif suffix == ".csv":
            from tools.office_tools import csv_to_markdown
            _, content = csv_to_markdown(str(fp), tmp_dir)
        else:
            from tools.office_tools import docx_to_markdown
            _, content = docx_to_markdown(str(fp), tmp_dir)
        return content
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def index_directory(path: str, glob: str = "*", refresh: bool = False) -> str:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        return f"Path not found: {root}"

    table = _get_table()
    from tools.lance_lock import mutation_lock
    db_path = _resolve_db_path()

    # Build set of already-indexed paths to skip
    try:
        existing = set(table.to_pandas()["path"].tolist())
    except Exception:
        existing = set()

    indexed = 0
    skipped = 0
    rows = []

    for fp in root.rglob(glob):
        if not fp.is_file():
            continue
        if any(part in _SKIP_DIRS for part in fp.parts):
            continue
        if fp.suffix.lower() in _SKIP_EXTS:
            continue
        str_path = str(fp)
        if refresh and str_path in existing:
            escaped = str_path.replace("'", "''")
            try:
                with mutation_lock(db_path):
                    table.delete(f"path = '{escaped}'")
            except Exception:
                pass
            existing.discard(str_path)
        if str_path in existing:
            skipped += 1
            continue
        try:
            text = _extract_text(fp)
        except Exception:
            continue
        if not text.strip():
            continue

        for i, chunk in enumerate(_chunk(text)):
            if not chunk.strip():
                continue
            try:
                vec = _embed(chunk)
            except Exception:
                continue
            rows.append({
                "id": _file_id(str_path, i),
                "path": str_path,
                "chunk_index": i,
                "text": chunk,
                "vector": vec,
            })
        indexed += 1

        # Batch write every 20 files
        if len(rows) >= 100:
            with mutation_lock(db_path):
                table.add(rows)
            rows = []

    if rows:
        with mutation_lock(db_path):
            table.add(rows)

    return (
        f"Indexed {indexed} files from {root} "
        f"({skipped} already indexed, skipped)."
    )


_REWRITE_MODEL = "qwen3:8b"  # qwen3.5:4b named in CLAUDE.md wasn't actually pulled; qwen3:8b pulled 2026-08-19


def _amplify_query(query: str) -> list[str]:
    """Ask the local rewrite model for 2 alternate phrasings to widen recall.
    Falls back to just the original query on any failure (offline, timeout, etc)."""
    try:
        resp = ollama.chat(
            model=_REWRITE_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    "Rewrite this search query as 2 alternate phrasings that use "
                    "different but related wording (synonyms, rephrasing). "
                    "One per line, no numbering, no explanation.\n\nQuery: " + query
                ),
            }],
            options={"num_predict": 80},
            think=False,
        )
        text = resp["message"]["content"].strip()
        alts = [ln.strip("-* \t") for ln in text.splitlines() if ln.strip()]
        return [query] + alts[:2]
    except Exception:
        return [query]


import re as _re

_SYMBOL_RE = _re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _lexical_overlap(query: str, text: str) -> float:
    """Cheap token-overlap score, case-insensitive."""
    qtok = set(query.lower().split())
    ttok = set(text.lower().split())
    if not qtok:
        return 0.0
    return len(qtok & ttok) / len(qtok)


_RERANK_URL = "http://127.0.0.1:8090/v1/rerank"


def _rerank_llamacpp(query: str, candidates: list[str]) -> list[float] | None:
    """Score candidates with a real cross-encoder (bge-reranker-v2-m3 served by
    llama-server --reranking on :8090 — Ollama has no rerank endpoint, this is
    the actual classification-head score, not an embedding-similarity proxy).
    Returns None (caller falls back to the heuristic) if the server isn't up."""
    import json
    import urllib.request

    body = json.dumps({"model": "bge-reranker-v2-m3", "query": query, "documents": candidates}).encode()
    req = urllib.request.Request(_RERANK_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    scores = [0.0] * len(candidates)
    for r in data["results"]:
        scores[r["index"]] = r["relevance_score"]
    return scores


def _symbol_overlap(query: str, text: str) -> float:
    """Fraction of query identifier-shaped tokens (CamelCase, snake_case, dotted
    API names) that appear VERBATIM (case-sensitive) in the text — catches exact
    API symbol matches like 'GeometryNodeTree' or 'is_mode_edit' that generic
    lexical overlap (lowercased, whitespace-only) misses or under-weights."""
    qsyms = {s for s in _SYMBOL_RE.findall(query) if len(s) > 2 and (s != s.lower() or "_" in s)}
    if not qsyms:
        return 0.0
    hits = sum(1 for s in qsyms if s in text)
    return hits / len(qsyms)


def semantic_file_search(query: str, path_filter: str = "", top_k: int = 5) -> str:
    try:
        table = _get_table()
        count = table.count_rows()
    except Exception:
        return "No files indexed yet. Use index_directory first."

    if count == 0:
        return "No files indexed yet. Use index_directory first."

    queries = _amplify_query(query)

    # search with each phrasing, merge candidates keeping the best (lowest) distance per chunk id
    best: dict[str, dict] = {}
    for q in queries:
        try:
            vec = _embed(q)
        except Exception:
            continue
        hits = table.search(vec).limit(top_k * 4).to_pandas()
        for _, row in hits.iterrows():
            rid = row["id"]
            if rid not in best or row["_distance"] < best[rid]["_distance"]:
                best[rid] = row.to_dict()

    if not best:
        return f"Embedding failed for query: '{query}'"

    import pandas as pd
    results = pd.DataFrame(best.values())

    if path_filter:
        results = results[results["path"].str.contains(path_filter, regex=False)]

    if results.empty:
        return f"No results for '{query}'" + (f" under {path_filter}" if path_filter else "")

    # rerank: prefer a real cross-encoder (llama-server --reranking) over vector
    # distance — distance alone put the actual GeometryNodeTree API page at rank
    # 7/10 for a query naming its exact symbols. Falls back to the distance +
    # symbol/lexical-overlap heuristic if the rerank server isn't running.
    rerank_scores = _rerank_llamacpp(query, results["text"].tolist())
    if rerank_scores is not None:
        results["_score"] = [-s for s in rerank_scores]  # sort ascending = best first
    else:
        results["_lex"] = results["text"].apply(lambda t: _lexical_overlap(query, t))
        results["_sym"] = results["text"].apply(lambda t: _symbol_overlap(query, t))
        # raw vector distance is on an unbounded/uncalibrated scale (nomic-embed-text
        # L2, seen ranging ~200-250 within one result set) — min-max normalize it into
        # [0,1] per query so the lexical/symbol bonuses (already 0-1) are comparable
        # instead of being drowned out by a ~30-unit distance spread.
        dmin, dmax = results["_distance"].min(), results["_distance"].max()
        span = dmax - dmin
        results["_dnorm"] = (results["_distance"] - dmin) / span if span > 0 else 0.0
        results["_score"] = results["_dnorm"] - 0.35 * results["_sym"] - 0.15 * results["_lex"]
    results = results.sort_values(by="_score", ascending=True)
    results = results.head(top_k)

    parts = []
    for _, row in results.iterrows():
        parts.append(
            f"File: {row['path']} (chunk {row['chunk_index']})\n"
            f"{row['text'][:400].strip()}"
        )

    return "\n\n---\n\n".join(parts)


def remove_file(path: str) -> None:
    """Remove every LanceDB chunk for one exact resolved source path."""
    table = _get_table()
    escaped = str(Path(path).expanduser().resolve()).replace("'", "''")
    from tools.lance_lock import mutation_lock
    with mutation_lock(_resolve_db_path()):
        table.delete(f"path = '{escaped}'")
