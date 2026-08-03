"""PDF processing utilities: convert PDFs to markdown and extract embedded images."""

from __future__ import annotations

import json
import os
from pathlib import Path

import fitz  # pymupdf


def _resolve_pdf_path(path_str: str) -> Path:
    """Resolve a PDF file path, handling ~ and LLM-expanded /home/user/ paths."""
    p = Path(path_str).expanduser()
    if not p.exists():
        home = os.path.expanduser("~")
        if not str(p).startswith(home) and path_str.startswith("/home/"):
            fallback = Path(home) / "/".join(Path(path_str).parts[3:])
            if fallback.exists():
                return fallback.resolve()
    return p.resolve()


def pdf_to_markdown(pdf_path: str, output_dir: str | None = None) -> tuple[str, str]:
    """Convert a PDF file to a markdown document.

    Args:
        pdf_path: Absolute or relative path to the source PDF file.
        output_dir: Directory where the .md file will be written.
                    Defaults to the same directory as the PDF.

    Returns:
        A tuple of (md_file_path_str, md_content_str).

    Raises:
        FileNotFoundError: If the PDF does not exist.
    """
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    out_dir = Path(output_dir) if output_dir is not None else p.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    with fitz.open(str(p)) as doc:
        md_parts: list[str] = [f"# {p.stem}\n"]

        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                md_parts.append(f"\n## Page {page_num}\n\n{text}")

    md_content = "\n".join(md_parts)
    md_file = out_dir / f"{p.stem}.md"
    md_file.write_text(md_content, encoding="utf-8")

    return (str(md_file), md_content)


def extract_pdf_images(pdf_path: str, output_dir: str) -> list[str]:
    """Extract all embedded raster images from a PDF and save them as PNG files.

    Args:
        pdf_path: Absolute or relative path to the source PDF file.
        output_dir: Directory where extracted PNG images will be saved.

    Returns:
        A list of absolute file-path strings for every saved PNG.
        Returns an empty list if the PDF contains no images.

    Raises:
        FileNotFoundError: If the PDF does not exist.
    """
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    img_counter = 1

    with fitz.open(str(p)) as doc:
        for page in doc:
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                img_fitz = fitz.Pixmap(doc, xref)

                # CMYK or other non-RGB(A) colour spaces → convert to RGB
                if img_fitz.n >= 4:
                    img_fitz = fitz.Pixmap(fitz.csRGB, img_fitz)

                out_path = out_dir / f"{p.stem}_img{img_counter}.png"
                img_fitz.save(str(out_path))
                saved.append(str(out_path))
                img_counter += 1

    return saved


def detect_pdf_form_fields(path: str) -> str:
    """Inspect a PDF for AcroForm interactive fields and return their metadata as JSON.

    Args:
        path: Absolute or relative path to the PDF file.

    Returns:
        A JSON-formatted string listing every form field found, or a plain message
        if the PDF has no interactive fields. XFA-based forms are not supported.

    Raises:
        FileNotFoundError: If the PDF does not exist.
    """
    p = _resolve_pdf_path(path)
    if not p.exists():
        return f"PDF not found: {path}"

    fields: list[dict] = []
    with fitz.open(str(p)) as doc:
        for page_num, page in enumerate(doc, start=1):
            for widget in page.widgets():
                entry: dict = {
                    "name": widget.field_name,
                    "type": widget.field_type_string,
                    "current_value": widget.field_value,
                    "page": page_num,
                    "required": bool(widget.field_flags & 2),
                    "options": (
                        list(widget.choice_values or [])
                        if widget.field_type_string in ("ListBox", "ComboBox")
                        else []
                    ),
                }
                fields.append(entry)

    if not fields:
        return (
            "This PDF contains no interactive AcroForm fields. "
            "Note: XFA-based dynamic forms are not supported."
        )

    return json.dumps({"fields": fields, "total": len(fields)}, indent=2, ensure_ascii=False)


def fill_pdf_form(
    path: str,
    fields: dict | str,
    output_path: str | None = None,
) -> str:
    """Fill AcroForm fields in a PDF and save to a new file.

    Args:
        path: Absolute or relative path to the source PDF.
        fields: A mapping of {field_name: value} to set. May be passed as a
                JSON string (the executor layer handles the parse before calling
                this function, but the function also accepts a raw string as a
                convenience for direct Python callers).
        output_path: Where to write the filled PDF. Defaults to
                     ``{stem}_filled.pdf`` in the same directory as the source.

    Returns:
        A plain-text confirmation message listing filled and skipped fields.

    Raises:
        FileNotFoundError: If the source PDF does not exist.
        ValueError: If ``fields`` is a string that cannot be parsed as JSON.
    """
    p = _resolve_pdf_path(path)
    if not p.exists():
        return f"PDF not found: {path}"

    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except json.JSONDecodeError as exc:
            return f"fields must be a JSON object string or dict, got: {exc}"

    out = Path(output_path) if output_path else p.parent / f"{p.stem}_filled.pdf"

    filled: list[str] = []
    skipped: list[str] = []

    with fitz.open(str(p)) as doc:
        # Iterate through all widgets and update them immediately (within page context)
        # to avoid widget unbinding issues
        for page in doc:
            for widget in page.widgets():
                if widget.field_name in fields:
                    widget.field_value = fields[widget.field_name]
                    widget.update()
                    if widget.field_name not in filled:
                        filled.append(widget.field_name)

        # Track skipped fields (requested but not found)
        for field_name in fields:
            if field_name not in filled:
                skipped.append(field_name)

        doc.save(str(out))

    lines = [f"Saved filled PDF → {out}"]
    if filled:
        lines.append(f"Fields filled ({len(filled)}): {', '.join(filled)}")
    if skipped:
        lines.append(f"Fields skipped — not found in PDF ({len(skipped)}): {', '.join(skipped)}")
    return "\n".join(lines)
