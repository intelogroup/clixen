"""One local retrieval seam over glob, extraction, full-text, and semantic evidence."""

from __future__ import annotations

import json
import fnmatch
from pathlib import Path

from agents.document_evidence import chunk_evidence, rank_evidence
from tools.structured import read_document


DOCUMENT_RETRIEVE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "document_retrieve",
        "description": "Find relevant evidence in documents under a scoped local workspace. Returns bounded cited chunks.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Question or exact terms to find"},
                "workspace": {"type": "string", "description": "Allowed workspace root"},
                "path_glob": {"type": "string", "default": "**/*", "description": "Document filename filter"},
                "limit": {"type": "integer", "default": 8, "description": "Maximum evidence chunks"},
                "search_mode": {"type": "string", "enum": ["lexical", "hybrid"], "default": "hybrid"},
            },
            "required": ["query", "workspace"],
        },
    },
}


def _matches_glob(relative: str, pattern: str) -> bool:
    """Match pathlib-style recursive defaults consistently across platforms."""
    return fnmatch.fnmatch(relative, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatch(relative, pattern[3:])
    )


def document_retrieve(query: str, workspace: str, path_glob: str = "**/*", limit: int = 8, search_mode: str = "hybrid") -> str:
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        return f"[error] workspace is not a directory: {root}"
    if Path(path_glob).is_absolute() or ".." in Path(path_glob).parts:
        return "[error] path_glob must stay inside workspace"
    allowed = {".pdf", ".docx", ".xlsx", ".pptx", ".rtf", ".html", ".htm", ".eml", ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".zip"}
    chunks = []
    from tools.document_manifest import is_quarantined
    for path in sorted(root.glob(path_glob))[:64]:
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        if is_quarantined(path):
            continue
        text = read_document(str(path), max_chars=12000)
        chunks.extend(chunk_evidence(str(path), text))
    if search_mode == "hybrid":
        chunks.extend(_indexed_chunks(query, str(root), path_glob, start=len(chunks)))
    # The same chunk can arrive from direct extraction, FTS, and semantic search.
    # Collapse it before ranking so the evidence budget represents distinct facts.
    unique = {}
    for chunk in chunks:
        key = (chunk.get("source", ""), chunk.get("locator", ""), chunk.get("text", ""))
        unique.setdefault(key, chunk)
    chunks = list(unique.values())
    for index, chunk in enumerate(chunks, 1):
        chunk["citation"] = f"E{index}"
    ranked = rank_evidence(query, chunks, limit=max(1, min(int(limit), 20)))
    return json.dumps(ranked, ensure_ascii=False)


def _indexed_chunks(query: str, workspace: str, path_glob: str, start: int) -> list[dict[str, str]]:
    """Optionally add existing FTS/vector hits; missing indexes are harmless."""
    results = []
    searches = []
    try:
        from tools.fulltext_search import fulltext_search
        searches.append(fulltext_search(query, path_filter=workspace, top_k=8))
    except Exception:
        pass
    try:
        from tools.semantic_files import semantic_file_search
        searches.append(semantic_file_search(query, path_filter=workspace, top_k=8))
    except Exception:
        pass
    for raw in searches:
        for block in str(raw).split("\n\n---\n\n"):
            if not block.startswith("File:") or "\n" not in block:
                continue
            header, text = block.split("\n", 1)
            source = header[5:].split(" (", 1)[0].strip()
            try:
                relative = str(Path(source).resolve().relative_to(Path(workspace).resolve()))
            except ValueError:
                continue
            from tools.document_manifest import is_active_version, is_quarantined
            if is_quarantined(source) or not is_active_version(source):
                continue
            if not _matches_glob(relative, path_glob):
                continue
            results.append({
                "citation": f"E{start + len(results) + 1}",
                "source": source,
                "locator": header.split("(", 1)[1].rstrip(")") if "(" in header else "indexed result",
                "text": text.strip(),
            })
    return results
