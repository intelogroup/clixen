"""
OCR tool — extracts text from image files via PaddleOCR v3.x.

Supports: PNG, JPG, JPEG, TIFF, BMP, WebP (anything OpenCV can decode).
Returns extracted text as plain lines, one per detected text region.

For structured/table-heavy scanned docs, prefer tools/surya_ocr.py instead —
Surya wins that benchmark; PaddleOCR here is the cheap default/fallback path.

API note: PaddleOCR 3.x uses .predict() instead of .ocr(), returns
OCRResult objects with rec_texts / rec_scores lists.
"""
import os
import shutil
import subprocess
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


def _tesseract(image_path: str, lang: str) -> str:
    binary = shutil.which("tesseract")
    if not binary:
        raise FileNotFoundError("tesseract is not installed")
    tess_lang = {"en": "eng", "fr": "fra"}.get(lang, lang or "eng")
    result = subprocess.run(
        [binary, image_path, "stdout", "-l", tess_lang, "--psm", "3"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    text = result.stdout.strip()
    if result.returncode != 0 or not text:
        raise RuntimeError(result.stderr.strip() or "tesseract detected no text")
    return text


def execute(image_path: str, lang: str = "en") -> str:
    """Extract text using Surya first, then Tesseract, then PaddleOCR."""
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

    errors = []
    try:
        from tools.surya_ocr import _ocr_daemon
        return _ocr_daemon(str(path), mode="ocr")
    except Exception as exc:
        errors.append(f"Surya: {exc}")

    try:
        return _tesseract(str(path), lang)
    except Exception as exc:
        errors.append(f"Tesseract: {exc}")

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
        errors.append("PaddleOCR is not installed")
        return "OCR unavailable. " + "; ".join(errors)
    except Exception as e:
        errors.append(f"PaddleOCR: {e}")
        return "OCR failed. " + "; ".join(errors)
