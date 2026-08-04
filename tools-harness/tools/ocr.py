"""
OCR tool — extracts text from image files.

Primary backend: the warm Unlimited-OCR daemon (unlimited_ocr_daemon.py, a 6.7B
VLM on MPS) — one-shot full-document parsing with layout. Falls back to
PaddleOCR v3.x when the daemon is unreachable/disabled.

Supports: PNG, JPG, JPEG, TIFF, BMP, WebP (anything OpenCV can decode).
Returns extracted text as plain lines, one per detected text region.

API note: PaddleOCR 3.x uses .predict() instead of .ocr(), returns
OCRResult objects with rec_texts / rec_scores lists.
"""
import json
import os
import re
from pathlib import Path

# Skip network connectivity check on every init — weights are cached locally
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

_ocr_instances: dict = {}

# PaddleOCR's native image decoder doesn't validate input format — feeding it
# a PDF (or anything else non-image) corrupted the process heap and crashed
# the whole bot with a malloc free-list checksum failure (confirmed via macOS
# crash report, 2026-07-07). Reject unsupported extensions before they ever
# reach paddleocr.
_SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}

# Warm Unlimited-OCR daemon (unlimited_ocr_daemon.py). Disable with
# UNLIMITED_OCR_DISABLE=1 to force the PaddleOCR fallback.
_DAEMON_URL = os.environ.get("UNLIMITED_OCR_URL", "http://127.0.0.1:9239").rstrip("/")
_DAEMON_DISABLED = os.environ.get("UNLIMITED_OCR_DISABLE", "0") == "1"
_DAEMON_TIMEOUT = float(os.environ.get("UNLIMITED_OCR_TIMEOUT", "600"))

# Strip the model's <|det|>type [bbox]<|/det|> markers into clean blocks — group
# lines of the same block with \n, separate blocks with \n\n (OmniDocBench
# post-processor from the Unlimited-OCR README).
_DET_RE = re.compile(r"<\|det\|>([^<\s]+)(?:\s*\[[^\]]*\])?\s*<\|/det\|>(.*)", re.DOTALL)


def _remove_det(raw: str) -> str:
    blocks: list[str] = []
    cur: list[str] | None = None
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            continue
        m = _DET_RE.match(line)
        if m:
            category, content = m.group(1).strip(), m.group(2).strip()
            if category == "image":
                continue
            if cur is not None:
                blocks.append(cur)
            cur = [content] if content else []
            continue
        if cur is None:
            cur = []
        cur.append(line)
    if cur is not None:
        blocks.append(cur)
    return "\n\n".join("\n".join(b) for b in blocks).strip()


def _ocr_daemon(image_path: str) -> str:
    """OCR via the warm Unlimited-OCR daemon. Raises on any failure so the
    caller can fall back to PaddleOCR."""
    import urllib.error
    import urllib.request

    if _DAEMON_DISABLED:
        raise ConnectionError("Unlimited-OCR daemon disabled (UNLIMITED_OCR_DISABLE=1)")
    body = json.dumps({"image_path": image_path}).encode()
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
        raise ConnectionError(f"Unlimited-OCR daemon HTTP {e.code}: {e.read()[:200]!r}") from e
    if "error" in data:
        raise ConnectionError(f"Unlimited-OCR daemon error: {data['error']}")
    text = (data.get("text") or "").strip()
    if not text:
        return "No text detected in image."
    clean = _remove_det(text)
    return clean or "No text detected in image."


def _get_ocr(lang: str) -> "PaddleOCR":
    if lang not in _ocr_instances:
        import warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        from paddleocr import PaddleOCR
        _ocr_instances[lang] = PaddleOCR(use_textline_orientation=True, lang=lang)
    return _ocr_instances[lang]


SCHEMA = {
    "type": "function",
    "function": {
        "name": "ocr_image",
        "description": (
            "Extract text from an image file using OCR. "
            "Use when the user provides an image path and wants to read text from it, "
            "or asks to OCR / scan / read text in a photo, screenshot, or document image. "
            "Returns the extracted text as plain lines."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "Absolute path to the image file (PNG, JPG, TIFF, BMP, WebP)"
                },
                "lang": {
                    "type": "string",
                    "description": "Language code: 'en' (default), 'fr', 'ch' (Chinese), 'ja', etc.",
                    "default": "en"
                }
            },
            "required": ["image_path"]
        }
    }
}


def execute(image_path: str, lang: str = "en") -> str:
    """Extract text from an image: Unlimited-OCR daemon first, PaddleOCR fallback."""
    path = Path(image_path)
    if not path.exists():
        return f"File not found: {image_path}"
    if not path.is_file():
        return f"Not a file: {image_path}"
    if path.suffix.lower() not in _SUPPORTED_EXTS:
        return (
            f"Unsupported file type '{path.suffix}' — ocr_image only handles image files "
            f"({', '.join(sorted(_SUPPORTED_EXTS))}). For a PDF, use pdf_to_markdown instead."
        )

    try:
        return _ocr_daemon(str(path))
    except Exception as e:
        if _DAEMON_DISABLED:
            pass  # disabled is not a real failure — go straight to PaddleOCR
        else:
            # Unreachable/unloaded daemon is fine — the harness isn't running it.
            import logging
            logging.getLogger("tools.ocr").debug("Unlimited-OCR daemon unavailable (%s), using PaddleOCR", e)

    try:
        ocr = _get_ocr(lang)
        results = ocr.predict(str(path))

        if not results:
            return "No text detected in image."

        lines = []
        for page in results:
            if isinstance(page, dict):
                texts = page.get("rec_texts") or []
                scores = page.get("rec_scores") or []
            else:
                texts = getattr(page, "rec_texts", None) or []
                scores = getattr(page, "rec_scores", None) or []
            for text, score in zip(texts, scores):
                if score >= 0.5:
                    lines.append(text)

        if not lines:
            return "No text detected with sufficient confidence."

        return "\n".join(lines)
    except ImportError:
        return "PaddleOCR is not installed. Run: pip install paddlepaddle paddleocr"
    except Exception as e:
        return f"OCR failed: {e}"
