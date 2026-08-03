"""docling wrapper — layout-aware document parsing for the agent.

docling (IBM, MIT) parses PDFs, DOCX, PPTX, HTML, images, and Markdown
with layout, reading order, tables, and formula extraction. It runs locally
on Apple Silicon (MPS where available, CPU fallback).

We expose it as `parse_document` — the agent calls it to get clean Markdown
from any document, replacing the noisier pymupdf-based path for messy or
table-heavy files.

First-call cost: docling downloads layout/table models (~hundreds of MB) on
first use. Subsequent calls are fast. We import lazily so the harness boot
isn't slowed.
"""
from __future__ import annotations

from pathlib import Path

_DEFAULT_MAX_CHARS = 12000


SCHEMA = {
    "type": "function",
    "function": {
        "name": "parse_document",
        "description": (
            "Layout-aware extraction of any PDF, DOCX, PPTX, HTML, or image into clean Markdown. "
            "Handles tables, multi-column layouts, headings, and embedded figures. "
            "Use for: contracts, papers, scanned invoices, slide decks. "
            "Better than the legacy pdf reader for structured docs. "
            "Returns Markdown text (truncated to max_chars)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file. ~ is expanded.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": f"Truncate output to this many chars (default {_DEFAULT_MAX_CHARS}).",
                    "default": _DEFAULT_MAX_CHARS,
                },
            },
            "required": ["path"],
        },
    },
}


_SUPPORTED_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".markdown",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
}


def execute(path: str, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    p = Path(path or "").expanduser()
    if not p.exists():
        return f"[parse_document error] file not found: {p}"
    if not p.is_file():
        return f"[parse_document error] not a file: {p}"
    if p.suffix.lower() not in _SUPPORTED_EXTS:
        return (
            f"[parse_document error] unsupported extension {p.suffix!r}. "
            f"Supported: {sorted(_SUPPORTED_EXTS)}"
        )

    try:
        from docling.document_converter import DocumentConverter  # lazy
    except ImportError:
        return (
            "[parse_document error] docling not installed. "
            "Run: pip install docling"
        )
    except Exception as e:
        return f"[parse_document error] docling import failed: {e}"

    try:
        converter = DocumentConverter()
        result = converter.convert(str(p))
    except Exception as e:
        return f"[parse_document error] conversion failed: {e}"

    try:
        md = result.document.export_to_markdown()
    except Exception as e:
        return f"[parse_document error] export failed: {e}"

    cap = max(500, min(int(max_chars or _DEFAULT_MAX_CHARS), 60000))
    if len(md) > cap:
        return md[:cap] + f"\n\n... [truncated; {len(md) - cap} more chars]"
    return md
