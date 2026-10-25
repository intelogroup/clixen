"""
OCR tool — extracts text from image files.

Backends, in the order they are tried:

1. **macOS Vision** (default): the framework Apple ships with the OS. No model
   download, no daemon, no network, no Homebrew. Measured on this machine at
   ~30 ms per image warm and it is the only engine that read coloured retina UI
   text — 4/4 visible sentences on a live screenshot where Tesseract recovered
   0/4. That matters because most OCR here is reading a *screen*.
2. **Tesseract**: cheap and slightly faster on plain black-on-white print
   (98.6% vs Vision's 100% on a synthetic page), so it stays as the fallback for
   bulk printed documents — and as the only option off macOS.
3. **Surya**: layout-aware HTML for scanned documents with tables and
   multi-column flow. It needs its own daemon and a multi-GB VLM, so it is used
   only where its structure is the point.

The chain used to be Surya → Tesseract → PaddleOCR, which meant that with the
Surya daemon down *and* Tesseract off the launchd PATH (it lives in
/opt/homebrew/bin), `ocr_image` simply returned "OCR unavailable" inside the
shipped app. Vision cannot fail that way: it is part of the OS.

Supports: PNG, JPG, JPEG, TIFF, BMP, WebP.
"""
import os
import shutil
import subprocess
import sys
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

# Set CLIXEN_OCR_ENGINE=vision|tesseract|surya|paddle to force one backend
# (diagnostics, benchmarks, and the case where one engine reads a page better).
_ENGINE_ENV = "CLIXEN_OCR_ENGINE"


def _vision(image_path: str, lang: str = "en") -> str:
    """Apple's Vision OCR. The PyObjC bindings module is not installed, so the
    framework's classes are bound dynamically — it is already on every macOS.

    Raises on any problem so the caller can fall through to the next backend.
    """
    if sys.platform != "darwin":
        raise RuntimeError("macOS only")
    import objc
    import Quartz
    from Foundation import NSBundle, NSURL

    bundle = NSBundle.bundleWithPath_("/System/Library/Frameworks/Vision.framework")
    if bundle is None or not bundle.load():
        raise RuntimeError("Vision framework not loadable")
    handler_cls = objc.lookUpClass("VNImageRequestHandler")
    request_cls = objc.lookUpClass("VNRecognizeTextRequest")

    url = NSURL.fileURLWithPath_(str(image_path))
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    if source is None:
        raise RuntimeError(f"cannot read image: {image_path}")
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if image is None:
        raise RuntimeError(f"cannot decode image: {image_path}")

    request = request_cls.alloc().init()
    request.setRecognitionLevel_(0)          # 0 = accurate, 1 = fast
    request.setUsesLanguageCorrection_(True)
    ok = handler_cls.alloc().initWithCGImage_options_(image, None).performRequests_error_([request], None)
    if not ok:
        raise RuntimeError("Vision could not process the image")

    lines = []
    for obs in (request.results() or []):
        candidates = obs.topCandidates_(1)
        if candidates and len(candidates):
            lines.append(str(candidates[0].string()))
    if not lines:
        raise RuntimeError("Vision detected no text")
    return "\n".join(lines)


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
        # A launchd-started sidecar inherits a bare PATH, so a Homebrew install is
        # invisible even though it is on the user's shell PATH — the same trap that
        # made release builds fail on /opt/homebrew/bin/rustc. Check the two usual
        # prefixes before declaring tesseract absent.
        for candidate in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
            if Path(candidate).is_file():
                binary = candidate
                break
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
    """Extract text from an image, trying the cheapest capable engine first.

    Vision (OS-native, best on screen text) → Tesseract (fine print, non-macOS)
    → Surya (layout/table documents) → PaddleOCR. `CLIXEN_OCR_ENGINE` pins one.
    """
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

    forced = os.environ.get(_ENGINE_ENV, "").strip().lower()
    errors = []

    def attempt(name: str, fn) -> str | None:
        try:
            return fn()
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            return None

    chain = [
        ("Vision", lambda: _vision(str(path), lang)),
        ("Tesseract", lambda: _tesseract(str(path), lang)),
        ("Surya", lambda: _surya(str(path))),
        ("PaddleOCR", lambda: _paddle(str(path), lang)),
    ]
    if forced:
        chain = [c for c in chain if c[0].lower().startswith(forced)]
        if not chain:
            return f"Unknown OCR engine '{forced}'. Use vision, tesseract, surya or paddle."

    for name, fn in chain:
        out = attempt(name, fn)
        if out:
            return out

    return "OCR unavailable. " + "; ".join(errors) if errors else "OCR unavailable."


def _surya(image_path: str) -> str:
    """Layout-aware OCR via the Surya daemon (its own venv, multi-GB VLM).

    Keeps a scanned document's structure (<table>, <h1>, reading order) instead of
    dumping flat lines, which is why it is still reached for on documents — but it
    needs a daemon that the harness does not supervise, so it is never first.
    """
    from tools.surya_ocr import _ocr_daemon

    return _ocr_daemon(image_path, mode="ocr")


def _paddle(image_path: str, lang: str = "en") -> str:
    """PaddleOCR v3.x — the last resort, and only where it is installed."""
    ocr = _get_ocr(lang)
    results = ocr.predict(image_path)
    if not results:
        raise RuntimeError("no text detected")

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
        raise RuntimeError("no text detected with sufficient confidence")
    return "\n".join(lines)
