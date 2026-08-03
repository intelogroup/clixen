"""
OCR tool — PaddleOCR v3.x extracts text from image files.

Supports: PNG, JPG, JPEG, TIFF, BMP, WebP (anything OpenCV can decode).
Returns extracted text as plain lines, one per detected text region.
Model weights are cached in ~/.paddlex/ on first call (~200 MB total).

API note: PaddleOCR 3.x uses .predict() instead of .ocr(), returns
OCRResult objects with rec_texts / rec_scores lists.
"""
import os
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


def execute(image_path: str, lang: str = "en") -> str:
    """Run PaddleOCR on the given image and return extracted text."""
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
