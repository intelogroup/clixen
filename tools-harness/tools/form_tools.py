"""Unified form processing: auto-detect PDF/DOCX and handle form operations.

Auto-fallback: if a PDF has no AcroForm fields, detect uses OCR+drawings+pixel
scan to find fields in flat PDFs (scanned/print-to-PDF forms). This allows
gemma4 to call a single tool (detect_form_fields / fill_form) for any PDF.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tools.pdf_tools import detect_pdf_form_fields, fill_pdf_form
from tools.docx_tools import detect_docx_form_fields, fill_docx_form
from tools.flat_pdf_tools import (
    detect_flat_pdf_fields,
    fill_flat_pdf,
)


def _resolve_form_path(path_str: str) -> Path:
    """Resolve a file path, handling ~ and LLM-expanded /home/user/ paths."""
    p = Path(path_str).expanduser()
    if not p.exists():
        home = os.path.expanduser("~")
        if not str(p).startswith(home) and path_str.startswith("/home/"):
            fallback = Path(home) / "/".join(Path(path_str).parts[3:])
            if fallback.exists():
                return fallback.resolve()
    return p.resolve()


def _has_acroform(p: Path) -> bool:
    """Check if a PDF has interactive AcroForm fields."""
    import fitz
    with fitz.open(str(p)) as doc:
        for page in doc:
            for _ in page.widgets():
                return True
    return False


def detect_form_fields(file_path: str) -> str:
    """Detect all form fields in a PDF or DOCX file.

    Auto-fallback: for PDFs without AcroForm, uses OCR+drawings+pixel scanning
    to detect fields in flat PDFs (handles CIDFont-hidden labels).

    Args:
        file_path: Absolute or relative path to PDF or DOCX file.

    Returns:
        JSON-formatted string listing all form fields with metadata.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If file type is unsupported.
    """
    p = _resolve_form_path(file_path)
    if not p.exists():
        return f"File not found: {file_path}"

    suffix = p.suffix.lower()
    if suffix == ".pdf":
        if _has_acroform(p):
            return detect_pdf_form_fields(str(p))
        else:
            return detect_flat_pdf_fields(str(p))
    elif suffix == ".docx":
        return detect_docx_form_fields(str(p))
    else:
        return f"Unsupported file type: {suffix}. Supported: .pdf, .docx"


def fill_form(
    file_path: str,
    fields: dict | str,
    output_path: str | None = None,
) -> str:
    """Fill form fields in a PDF or DOCX and save to a new file.

    Auto-fallback: for PDFs without AcroForm, runs flat PDF detection internally
    and maps the user's field values (keyed by label) to bbox positions.

    Args:
        file_path: Absolute or relative path to PDF or DOCX file.
        fields: Mapping of {field_name: value} or JSON string.
            For AcroForm: use exact widget names.
            For flat PDFs: use field labels from detect_form_fields output.
        output_path: Optional output path.

    Returns:
        Confirmation message with list of filled and skipped fields.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If file type is unsupported or fields JSON is invalid.
    """
    p = _resolve_form_path(file_path)
    if not p.exists():
        return f"File not found: {file_path}"

    suffix = p.suffix.lower()
    if suffix == ".pdf":
        if _has_acroform(p):
            return fill_pdf_form(str(p), fields, output_path)
        else:
            return _fill_flat_pdf_form(str(p), fields, output_path)
    elif suffix == ".docx":
        return fill_docx_form(str(p), fields, output_path)
    else:
        return f"Unsupported file type: {suffix}. Supported: .pdf, .docx"


def _fill_flat_pdf_form(
    path: str,
    fields: dict | str,
    output_path: str | None = None,
) -> str:
    """Internal: fill a flat PDF. Runs detection to get field defs, then fills."""
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except json.JSONDecodeError as exc:
            raise ValueError(f"fields must be JSON object or dict: {exc}")

    if output_path is None:
        output_path = str(Path(path).parent / f"{Path(path).stem}_filled.pdf")

    # Run detection to get field positions
    fields_json = detect_flat_pdf_fields(path)

    return fill_flat_pdf(path, fields_json, json.dumps(fields), output_path)


def update_form(
    file_path: str,
    field_updates: dict | str,
    output_path: str | None = None,
) -> str:
    """Update specific fields in an already-filled form.

    Reads the existing form, applies field updates (preserving other fields),
    and saves to a new file. Useful for partial updates to previously-filled forms.

    Args:
        file_path: Absolute or relative path to existing filled form (PDF or DOCX).
        field_updates: Mapping of {field_name: new_value} or JSON string.
        output_path: Optional output path. Defaults to {stem}_updated.{ext}.

    Returns:
        Confirmation message with updated field count.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If file type is unsupported or fields JSON is invalid.
    """
    p = _resolve_form_path(file_path)
    if not p.exists():
        return f"File not found: {file_path}"

    suffix = p.suffix.lower()

    if isinstance(field_updates, dict):
        pass
    elif isinstance(field_updates, str):
        try:
            field_updates = json.loads(field_updates)
        except json.JSONDecodeError as exc:
            return f"field_updates must be JSON object string or dict: {exc}"
    else:
        return f"field_updates must be a dict or JSON string, got {type(field_updates).__name__}"

    if not field_updates:
        return "No field updates provided."

    if output_path is None:
        output_path = str(p.parent / f"{p.stem}_updated{suffix}")

    if suffix == ".pdf":
        if _has_acroform(p):
            return fill_pdf_form(str(p), field_updates, output_path)
        else:
            return _fill_flat_pdf_form(str(p), field_updates, output_path)
    elif suffix == ".docx":
        return fill_docx_form(str(p), field_updates, output_path)
    else:
        return f"Unsupported file type: {suffix}. Supported: .pdf, .docx"
