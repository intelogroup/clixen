"""Deterministic document operations. No model calls."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path


COMPARE_DOCUMENTS_SCHEMA = {
    "type": "function", "function": {"name": "compare_documents",
    "description": "Compare two extracted local documents and return their line-level differences.",
    "parameters": {"type": "object", "properties": {
        "left_source": {"type": "string"}, "left_text": {"type": "string"},
        "right_source": {"type": "string"}, "right_text": {"type": "string"},
    }, "required": ["left_source", "left_text", "right_source", "right_text"]}}}

EXTRACT_FIELDS_SCHEMA = {
    "type": "function", "function": {"name": "extract_document_fields",
    "description": "Extract named fields from already-read document text.",
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}},
    }, "required": ["text", "fields"]}}}

DETECT_INCONSISTENCIES_SCHEMA = {
    "type": "function", "function": {"name": "detect_document_inconsistencies",
    "description": "Find conflicting labeled values across extracted documents.",
    "parameters": {"type": "object", "properties": {
        "documents": {"type": "object", "additionalProperties": {"type": "string"}},
    }, "required": ["documents"]}}}

CLASSIFY_DOCUMENT_SCHEMA = {
    "type": "function", "function": {"name": "classify_document",
    "description": "Classify a document by content and file format.",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string"}, "text": {"type": "string"},
    }, "required": ["path"]}}}

BATCH_INSPECT_DOCUMENTS_SCHEMA = {
    "type": "function", "function": {"name": "batch_inspect_documents",
    "description": "Inspect supported documents in a workspace, classify them, report sizes, and detect duplicate content without modifying files.",
    "parameters": {"type": "object", "properties": {
        "workspace": {"type": "string"}, "pattern": {"type": "string", "default": "**/*"},
        "limit": {"type": "integer", "default": 200}}, "required": ["workspace"]}}}


def compare_documents(left_source: str, left: str, right_source: str, right: str) -> str:
    diff = difflib.unified_diff(
        left.splitlines(), right.splitlines(),
        fromfile=left_source, tofile=right_source, lineterm="",
    )
    return "\n".join(diff)


def extract_fields(text: str, fields: list[str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for field in fields:
        pattern = re.compile(rf"^\s*{re.escape(field)}\s*[:#-]?\s*(.+?)\s*$", re.I | re.M)
        match = pattern.search(text)
        result[field] = match.group(1).strip() if match else None
    return result


def detect_inconsistencies(documents: dict[str, str]) -> list[dict]:
    values: dict[str, list[dict[str, str]]] = defaultdict(list)
    for source, text in documents.items():
        for line in text.splitlines():
            match = re.match(r"\s*([^:#-]{2,60}?)\s*[:#-]\s*(.+?)\s*$", line)
            if match:
                key, value = match.group(1).strip().lower(), match.group(2).strip()
                values[key].append({"source": source, "value": value})
    return [
        {"key": key, "sources": entries}
        for key, entries in values.items()
        if len({entry["value"].lower() for entry in entries}) > 1
    ]


def classify_document(path: str, text: str = "") -> dict[str, str]:
    suffix = Path(path).suffix.lower()
    lowered = text.lower()
    if "invoice" in lowered or "amount due" in lowered:
        kind = "invoice"
    elif "agreement" in lowered or "termination notice" in lowered:
        kind = "agreement"
    elif "resume" in lowered or "experience" in lowered:
        kind = "resume"
    else:
        kind = "other"
    return {"type": kind, "format": suffix.lstrip(".") or "unknown"}


def batch_inspect_documents(workspace: str, pattern: str = "**/*", limit: int = 200) -> str:
    """Return a bounded, read-only workspace inventory with duplicate detection."""
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        return f"[error] workspace is not a directory: {root}"
    if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        return "[error] pattern must stay inside workspace"
    supported = {".pdf", ".docx", ".xlsx", ".pptx", ".rtf", ".html", ".htm", ".eml", ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".zip"}
    rows, grouped = [], {}
    for path in sorted(root.glob(pattern)):
        if len(rows) >= max(1, min(int(limit), 500)):
            break
        if not path.is_file() or path.suffix.lower() not in supported:
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            info = classify_document(str(path))
            rows.append({"path": str(path), "format": info["format"], "type": info["type"], "bytes": path.stat().st_size, "fingerprint": digest})
            grouped.setdefault(digest, []).append(str(path))
        except OSError:
            continue
    duplicates = [{"fingerprint": key, "paths": paths} for key, paths in grouped.items() if len(paths) > 1]
    return json.dumps({"workspace": str(root), "documents": rows, "duplicates": duplicates}, ensure_ascii=False)
