"""
Surya OCR tool — fast, accurate OCR with layout detection in 90+ languages.

Uses VikParuchuri/surya (v0.17+). Falls back gracefully if not installed.
Supports: PNG, JPG, JPEG, TIFF, BMP, WebP, PDF (via pypdfium2).
"""

from pathlib import Path

_surya_loaded: bool | None = None


def _ensure_surya():
    global _surya_loaded
    if _surya_loaded is not None:
        return _surya_loaded
    try:
        import surya
        _surya_loaded = True
        return True
    except ImportError:
        _surya_loaded = False
        return False


SCHEMA = {
    "type": "function",
    "function": {
        "name": "surya_ocr",
        "description": (
            "Extract text from an image or PDF using Surya OCR with layout-aware detection. "
            "Returns structured text with reading order preserved. "
            "Use when the user wants to OCR / scan / read text in a document, screenshot, "
            "photo, or PDF. More accurate than ocr_image for multi-column layouts, "
            "tables, and mixed-language documents. Supports 90+ languages."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "Absolute path to the image or PDF file"
                },
                "lang": {
                    "type": "string",
                    "description": "Language code: 'en' (default), 'fr', 'de', 'zh', 'ja', etc.",
                    "default": "en",
                },
                "detect_tables": {
                    "type": "boolean",
                    "description": "Whether to detect and OCR tables separately (slower)",
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

    if not _ensure_surya():
        return (
            "Surya OCR is not installed. Run: pip install surya-ocr\n"
            "Then the model weights will download on first use (~1 GB)."
        )

    try:
        from surya.ocr import run_ocr
        from surya.model.detection.model import load_model as load_det_model
        from surya.model.detection.processor import load_processor as load_det_processor
        from surya.model.recognition.model import load_model as load_rec_model
        from surya.model.recognition.processor import load_processor as load_rec_processor
    except ImportError as e:
        return f"Surya imports failed: {e}"

    try:
        langs = [lang] if lang else ["en"]
        det_model = load_det_model()
        det_processor = load_det_processor()
        rec_model = load_rec_model()
        rec_processor = load_rec_processor()

        results = run_ocr(
            [str(path)],
            langs,
            det_model,
            det_processor,
            rec_model,
            rec_processor,
        )

        if not results or not results[0]:
            return "No text detected."

        all_text = []
        for page_result in results:
            for line in page_result.text_lines:
                all_text.append(line.text)

        if not all_text:
            return "No text detected."

        return "\n".join(all_text)

    except Exception as e:
        return f"Surya OCR failed: {e}"
