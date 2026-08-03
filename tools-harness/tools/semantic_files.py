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

_OFFICE_EXTS = {".pdf", ".xlsx", ".xls", ".docx", ".doc"}

_HOME = str(Path.home())
_DB_PATH = os.path.join(_HOME, ".cache", "clixen", "file_index.lance")
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
            "Search indexed files by meaning — finds relevant code, docs, or text "
            "even if the exact words don't match. Much better than grep for questions "
            "like 'where is authentication handled?' or 'find error handling logic'. "
            "Requires the directory to be indexed first with index_directory."
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
    db = lancedb.connect(_DB_PATH)
    if _TABLE in db.table_names():
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
        else:
            from tools.office_tools import docx_to_markdown
            _, content = docx_to_markdown(str(fp), tmp_dir)
        return content
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def index_directory(path: str, glob: str = "*") -> str:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        return f"Path not found: {root}"

    table = _get_table()

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
            table.add(rows)
            rows = []

    if rows:
        table.add(rows)

    return (
        f"Indexed {indexed} files from {root} "
        f"({skipped} already indexed, skipped)."
    )


def semantic_file_search(query: str, path_filter: str = "", top_k: int = 5) -> str:
    try:
        table = _get_table()
        count = table.count_rows()
    except Exception:
        return "No files indexed yet. Use index_directory first."

    if count == 0:
        return "No files indexed yet. Use index_directory first."

    try:
        vec = _embed(query)
    except Exception as e:
        return f"Embedding failed: {e}"

    results = (
        table.search(vec)
        .limit(top_k * 3)  # over-fetch to allow path filtering
        .to_pandas()
    )

    if path_filter:
        results = results[results["path"].str.contains(path_filter, regex=False)]

    results = results.head(top_k)

    if results.empty:
        return f"No results for '{query}'" + (f" under {path_filter}" if path_filter else "")

    parts = []
    for _, row in results.iterrows():
        parts.append(
            f"File: {row['path']} (chunk {row['chunk_index']})\n"
            f"{row['text'][:400].strip()}"
        )

    return "\n\n---\n\n".join(parts)
