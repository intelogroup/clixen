"""
Full-text keyword search — indexes file contents into an embedded Tantivy
index (BM25-ranked, sub-millisecond queries) for exact/keyword lookups that
semantic_file_search's vector similarity isn't suited for (error strings,
identifiers, exact phrases).

Two tools:
  index_directory_fts — crawl a directory and index files into Tantivy
  fulltext_search      — search indexed files by keyword (BM25)
"""

import hashlib
import shutil
import tempfile
from pathlib import Path

import tantivy

_OFFICE_EXTS = {".pdf", ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt", ".csv"}

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
              ".cache", "site-packages", "dist", "build"}
_SKIP_EXTS = {".pyc", ".pyo", ".so", ".dylib", ".bin", ".exe",
              ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".mp4",
              ".wav", ".ogg", ".zip", ".tar", ".gz",
              ".onnx", ".gguf", ".safetensors", ".pt", ".pth"}

# Same chunk size/overlap as tools/semantic_files.py — keeps the two indexes
# comparable and gives BM25 scoring per-section granularity instead of
# scoring (and returning) an entire file as one blob.
_CHUNK_CHARS = 1500
_CHUNK_OVERLAP = 200


def _chunk(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + _CHUNK_CHARS
        chunks.append(text[start:end])
        start += _CHUNK_CHARS - _CHUNK_OVERLAP
    return chunks


def _resolve_index_path() -> str:
    import os as _os

    override = _os.environ.get("CLIXEN_FTS_INDEX_PATH", "").strip()
    if override:
        return override
    from tools.vault_paths import db_path, ensure_data_dir

    ensure_data_dir()
    p = Path(db_path("file_index_fts"))
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


_SCHEMA = None
_INDEX = None


def _get_schema():
    global _SCHEMA
    if _SCHEMA is None:
        sb = tantivy.SchemaBuilder()
        sb.add_text_field("id", stored=True)
        # raw tokenizer -> exact-term match, needed since delete_documents("path", ...)
        # must hit every chunk sharing a path, not fuzzy/tokenized matches.
        sb.add_text_field("path", stored=True, tokenizer_name="raw")
        sb.add_text_field("body", stored=True)
        sb.add_integer_field("chunk_index", stored=True)
        _SCHEMA = sb.build()
    return _SCHEMA


def _get_index():
    global _INDEX
    if _INDEX is None:
        idx_dir = _resolve_index_path()
        schema = _get_schema()
        try:
            _INDEX = tantivy.Index.open(idx_dir)
        except Exception:
            _INDEX = tantivy.Index(schema, path=idx_dir)
    return _INDEX


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

INDEX_DIR_FTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "index_directory_fts",
        "description": (
            "Index a directory's files into a full-text (keyword/BM25) search "
            "index. Run this once on a folder before using fulltext_search on "
            "it. Re-running re-indexes files (cheap — no embedding calls)."
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

FULLTEXT_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fulltext_search",
        "description": (
            "Search indexed files by keyword/exact phrase (BM25-ranked) — a FIRST TOOL "
            "choice alongside semantic_file_search for open-ended content questions, not "
            "just literal lookups. Use for exact identifiers, error strings, names, or "
            "phrases — semantic_file_search is better for conceptual/meaning-based "
            "queries. Prefer these two search tools over list_directory/grep_files "
            "wandering when the user hasn't named a specific file path. Requires the "
            "directory to be indexed first with index_directory_fts (call that first if "
            "this returns no index found)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword or phrase to search for (supports AND/OR)",
                },
                "path_filter": {
                    "type": "string",
                    "description": "Optional: restrict results to files under this path",
                    "default": "",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

# ---------------------------------------------------------------------------
# Extraction (same converters as semantic_file_search, kept independent so
# either index can be rebuilt without the other)
# ---------------------------------------------------------------------------


def _extract_text(fp: Path) -> str:
    suffix = fp.suffix.lower()
    if suffix not in _OFFICE_EXTS:
        return fp.read_bytes()[:200_000].decode("utf-8", errors="replace")

    tmp_dir = tempfile.mkdtemp(prefix="ftsidx_")
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


def _doc_id(path: str, chunk_index: int) -> str:
    return hashlib.md5(f"{path}::{chunk_index}".encode()).hexdigest()

# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def index_directory_fts(path: str, glob: str = "*") -> str:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        return f"Path not found: {root}"

    index = _get_index()
    writer = index.writer()

    indexed = 0
    for fp in root.rglob(glob):
        if not fp.is_file():
            continue
        if any(part in _SKIP_DIRS for part in fp.parts):
            continue
        if fp.suffix.lower() in _SKIP_EXTS:
            continue
        try:
            text = _extract_text(fp)
        except Exception:
            continue
        if not text.strip():
            continue

        str_path = str(fp)
        writer.delete_documents("path", str_path)
        for i, chunk_text in enumerate(_chunk(text)):
            writer.add_document(tantivy.Document(
                id=_doc_id(str_path, i), path=str_path, body=chunk_text, chunk_index=i,
            ))
        indexed += 1

    writer.commit()
    index.reload()

    return f"Indexed {indexed} files from {root} into the full-text index."


def fulltext_search(query: str, path_filter: str = "", top_k: int = 5) -> str:
    index = _get_index()
    try:
        searcher = index.searcher()
        parsed = index.parse_query(query, ["body"])
        hits = searcher.search(parsed, top_k * 3 if path_filter else top_k).hits
    except Exception as e:
        return f"Search failed: {e}"

    if not hits:
        return f"No results for '{query}'" + (f" under {path_filter}" if path_filter else "")

    parts = []
    for score, addr in hits:
        doc = searcher.doc(addr)
        p = doc["path"][0]
        if path_filter and path_filter not in p:
            continue
        body = doc["body"][0]
        chunk_idx = doc["chunk_index"][0]
        idx = body.lower().find(query.split()[0].lower()) if query.split() else -1
        snippet = body[max(0, idx - 150):idx + 250] if idx != -1 else body[:400]
        parts.append(f"File: {p} (chunk {chunk_idx}, score {score:.2f})\n{snippet.strip()}")
        if len(parts) >= top_k:
            break

    if not parts:
        return f"No results for '{query}'" + (f" under {path_filter}" if path_filter else "")

    return "\n\n---\n\n".join(parts)


def remove_file(path: str) -> None:
    """Remove every exact source path from the Tantivy index."""
    index = _get_index()
    writer = index.writer()
    writer.delete_documents("path", str(Path(path).expanduser().resolve()))
    writer.commit()
    index.reload()
