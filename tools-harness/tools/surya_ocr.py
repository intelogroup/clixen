"""
Surya OCR tool — layout-aware, structured OCR for scanned documents with
tables/multi-column layout.

Backend: the warm Surya daemon (surya_daemon.py, surya-ocr 0.22.x — a VLM
running on llama.cpp in its OWN venv). The daemon returns structured HTML
(<table>/<h1>/<li>…) instead of a flat text dump, which preserves reading
order and table structure on scanned/multi-column documents. Falls back
gracefully when the daemon isn't running (the harness doesn't supervise it).

Supports: PNG, JPG, JPEG, TIFF, BMP, WebP (image files only — for PDFs the
read_specialist/structured path rasterizes and calls back in here per page).
"""

import json
import os
from pathlib import Path

_DAEMON_URL = os.environ.get("SURYA_URL", "http://127.0.0.1:9240").rstrip("/")
_DAEMON_DISABLED = os.environ.get("SURYA_DISABLE", "0") == "1"
_DAEMON_TIMEOUT = float(os.environ.get("SURYA_TIMEOUT", "900"))

_SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}


def _ocr_daemon(image_path: str, mode: str = "ocr") -> str:
    """OCR via the warm Surya daemon. Raises on any failure."""
    import urllib.error
    import urllib.request

    if _DAEMON_DISABLED:
        raise ConnectionError("Surya daemon disabled (SURYA_DISABLE=1)")
    body = json.dumps({"image_path": image_path, "mode": mode}).encode()
    req = urllib.request.Request(
        f"{_DAEMON_URL}/ocr",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_DAEMON_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise ConnectionError(f"Surya daemon HTTP {e.code}: {e.read()[:200]!r}") from e
    if "error" in data:
        raise ConnectionError(f"Surya daemon error: {data['error']}")
    return (data.get("text") or "").strip() or "No text detected in image."


SCHEMA = {
    "type": "function",
    "function": {
        "name": "surya_ocr",
        "description": (
            "Extract text from an image using Surya OCR with layout-aware detection. "
            "Returns structured text with reading order preserved (tables/multi-column "
            "come back as HTML markup, not a flat dump). "
            "Use when the user wants to OCR / scan / read text in a scanned document, "
            "photo, or screenshot — especially documents with TABLES, multi-column "
            "layouts, or mixed content. For a PDF, use read_document instead (it "
            "rasterizes scanned pages and calls OCR per page)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "Absolute path to the image file (PNG, JPG, TIFF, BMP, WebP)"
                },
                "detect_tables": {
                    "type": "boolean",
                    "description": "Use table-recognition mode (structured table output, slower)",
                    "default": False,
                },
            },
            "required": ["image_path"],
        },
    },
}


def execute(image_path: str, lang: str = "en", detect_tables: bool = False) -> str:
    path = Path(image_path)
    if not path.exists():
        return f"File not found: {image_path}"
    if not path.is_file():
        return f"Not a file: {image_path}"
    if path.suffix.lower() not in _SUPPORTED_EXTS:
        return (
            f"Unsupported file type '{path.suffix}' — surya_ocr only handles image files "
            f"({', '.join(sorted(_SUPPORTED_EXTS))}). For a PDF, use read_document."
        )

    try:
        return _ocr_daemon(str(path), mode="table" if detect_tables else "ocr")
    except Exception as e:
        import logging

        logging.getLogger("tools.surya_ocr").debug("Surya daemon unavailable (%s)", e)
        return (
            "Surya OCR is not available right now (daemon not running). "
            "Fall back to ocr_image, or ensure the Surya daemon is supervised by core.py."
        )
