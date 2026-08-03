"""
Form field detection and filling for PDFs without AcroForm fields.

Detects form fields visually using pdfplumber text/element analysis:
- Underline fields: sequences of underscore characters forming fill-in lines
- Checkboxes: small square rects (filled or unfilled)
- Labels: text preceding the field entry area on the same line

Fills fields by overlaying text at detected coordinates using PyMuPDF.

Usage workflow:
  1. vision_detect_form_fields("form.pdf")
     -> returns JSON with detected field labels, types, bounding boxes
  2. vision_fill_form_fields("form.pdf", fields_json, values_json, "filled.pdf")
     -> overlays values at detected bbox coordinates
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import fitz  # pymupdf

_log = logging.getLogger("vision_form_tools")

_MIN_UNDERSCORE_RUN = 5
_MIN_CHECKBOX_SIZE = 5
_MAX_CHECKBOX_SIZE = 20
_PDF_WIDTH = 612
_PDF_HEIGHT = 792

_UNDERSCORE_REGEX = re.compile(r"_+")


def _resolve_pdf_path(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    if not p.exists():
        home = os.path.expanduser("~")
        if not str(p).startswith(home) and path_str.startswith("/home/"):
            fallback = Path(home) / "/".join(Path(path_str).parts[3:])
            if fallback.exists():
                return fallback.resolve()
    return p.resolve()


def _classify_field_type(label: str) -> str:
    label_lower = label.strip().lower()
    if re.search(r"\bsign(ature)?\b", label_lower):
        return "signature"
    if re.search(r"\bdate\b", label_lower):
        return "date"
    return "text_input"


def _find_underscore_fields(page) -> list[dict]:
    """Detect form fields from consecutive underscore characters.

    Scans all chars, groups sequential underscores at the same y-position,
    determines the label from text on the same line before the underscores.
    """
    underscores = [c for c in page.chars if c["text"] == "_"]
    if not underscores:
        return []

    underscores.sort(key=lambda c: (c["top"], c["x0"]))

    runs: list[list] = []
    current_run: list = [underscores[0]]

    for c in underscores[1:]:
        last = current_run[-1]
        same_line = abs(c["top"] - last["top"]) < 4
        adjacent = (c["x0"] - last["x1"]) < 3

        if same_line and adjacent:
            current_run.append(c)
        else:
            runs.append(current_run)
            current_run = [c]
    runs.append(current_run)

    fields = []
    for run in runs:
        if len(run) < _MIN_UNDERSCORE_RUN:
            continue

        x0 = run[0]["x0"]
        x1 = run[-1]["x1"]
        y0 = run[0]["top"]
        y1 = run[0]["top"] + run[0].get("height", 12)

        # Find label: text on same line before the underscores.
        # Scan backwards from the underscore to find the nearest text fragment.
        label_start = run[0]["x0"] - 150  # look max 150px left
        same_row_chars = [
            c for c in page.chars
            if c["text"] != "_"
            and abs(c["top"] - run[0]["top"]) < 5
            and label_start <= c["x1"] <= run[0]["x0"]
        ]

        if not same_row_chars:
            label_text = ""
        else:
            same_row_chars.sort(key=lambda c: c["x0"])
            # Find the last gap > 20px (indicates separate text fragment)
            fragments = []
            current = [same_row_chars[0]]
            for c in same_row_chars[1:]:
                gap = c["x0"] - current[-1]["x1"]
                if gap > 25:
                    fragments.append(current)
                    current = [c]
                else:
                    current.append(c)
            fragments.append(current)

            # Use the fragment closest to the underscore
            closest = fragments[-1]
            label_text = "".join(c["text"] for c in closest).strip()
            label_text = label_text.rstrip(":").strip()
            # If fragment is too generic (< 3 chars), fall back to full text
            if len(label_text) < 3 and len(fragments) > 1:
                label_text = "".join(c["text"] for c in same_row_chars).strip().rstrip(":").strip()

        ftype = _classify_field_type(label_text)

        fields.append({
            "label": label_text or f"Input (y={y0:.0f})",
            "type": ftype,
            "page": page.page_number,
            "bbox": [x0, y0 - 2, x1, y1 + 2],
        })

    return fields


def _find_checkboxes(page) -> list[dict]:
    """Detect checkboxes from small square rects (filled or unfilled).

    Labels are extracted from page text lines, matched by vertical proximity
    (nearest text line above the rect).
    """
    text_lines = page.extract_text_lines()
    text_lines.sort(key=lambda l: l["top"])
    text_lines_y = [(l["top"], l["bottom"], l["text"]) for l in text_lines]

    fields = []
    seen_centers: set = set()

    for rect in page.rects:
        w = abs(rect["x1"] - rect["x0"])
        h = abs(rect["y1"] - rect["y0"])

        if not (_MIN_CHECKBOX_SIZE <= w <= _MAX_CHECKBOX_SIZE and _MIN_CHECKBOX_SIZE <= h <= _MAX_CHECKBOX_SIZE):
            continue

        aspect = max(w, h) / max(min(w, h), 1)
        if aspect > 2:
            continue

        rcx = round((rect["x0"] + rect["x1"]) / 2, 1)
        rcy = round((rect["y0"] + rect["y1"]) / 2, 1)
        key = (rcx, rcy)
        if key in seen_centers:
            continue

        boxes_at_y = [
            r for r in page.rects
            if _MIN_CHECKBOX_SIZE <= abs(r["x1"] - r["x0"]) <= _MAX_CHECKBOX_SIZE
            and _MIN_CHECKBOX_SIZE <= abs(r["y1"] - r["y0"]) <= _MAX_CHECKBOX_SIZE
            and abs((r["y0"] + r["y1"]) / 2 - rcy) < 15
        ]
        first_box = min(boxes_at_y, key=lambda r: r["x0"])

        for other in boxes_at_y:
            ocx = round((other["x0"] + other["x1"]) / 2, 1)
            ocy = round((other["y0"] + other["y1"]) / 2, 1)
            seen_centers.add((ocx, ocy))

        label_text = ""
        best_gap = 9999
        for top, bottom, text in text_lines_y:
            if bottom > rect["y0"]:
                continue
            gap = rect["y0"] - bottom
            if gap < best_gap:
                best_gap = gap
                label_text = text

        if not label_text or best_gap > 60:
            label_text = f"Checkbox {rcy:.0f}"

        fields.append({
            "label": label_text,
            "type": "checkbox",
            "page": page.page_number,
            "bbox": [first_box["x0"] - 2, rect["y0"] - 2, first_box["x1"] + 2, rect["y1"] + 2],
        })

    return fields


def vision_detect_form_fields(path: str, pages: str | None = None) -> str:
    """Detect form fields in a PDF using pdfplumber-based heuristic analysis.

    Works on scanned or print-to-PDF forms without interactive AcroForm fields.
    Detects text input fields (underscore sequences), checkboxes (small rects),
    signature fields, and date fields.

    Args:
        path: Absolute or relative path to the PDF file.
        pages: Page range (e.g. "1-3", "1"). All pages if None.

    Returns:
        JSON string with detected fields::
            {
                "fields": [{"label": "Name", "type": "text_input", "page": 1, "bbox": [...]}],
                "total": N,
                "pages_processed": [1, 2, ...]
            }
    """
    p = _resolve_pdf_path(path)
    if not p.exists():
        return f"PDF not found: {path}"

    _log.info("Processing %s", p)

    import pdfplumber
    with pdfplumber.open(str(p)) as pdf:
        total_pages = len(pdf.pages)

        if pages and "-" in pages:
            start_s, end_s = pages.split("-", 1)
            page_nums = list(range(max(1, int(start_s)), min(total_pages, int(end_s)) + 1))
        elif pages:
            page_nums = [int(pages)]
        else:
            page_nums = list(range(1, total_pages + 1))

        all_fields: list[dict] = []

        for page_num in page_nums:
            page = pdf.pages[page_num - 1]
            _log.info("Analyzing page %d/%d ...", page_num, total_pages)

            underscore_fields = _find_underscore_fields(page)
            checkboxes = _find_checkboxes(page)

            page_fields = underscore_fields + checkboxes

            seen: set = set()
            unique = []
            for f in page_fields:
                key = (f["label"], f["page"])
                if key not in seen:
                    seen.add(key)
                    unique.append(f)

            all_fields.extend(unique)
            _log.info("  Found %d fields on page %d", len(unique), page_num)

    return json.dumps(
        {
            "fields": all_fields,
            "total": len(all_fields),
            "pages_processed": page_nums,
        },
        indent=2,
        ensure_ascii=False,
    )


def vision_fill_form_fields(
    path: str,
    fields_json: str | list,
    values_json: str | dict,
    output_path: str | None = None,
    font_size: int = 10,
) -> str:
    """Fill a PDF form using detected field coordinates.

    Overlays text values at the bounding box coordinates detected by
    vision_detect_form_fields. Handles text inputs, checkboxes, and signature lines.

    Args:
        path: Absolute or relative path to the source PDF.
        fields_json: JSON string or list of detected field dicts.
        values_json: JSON string or dict mapping field labels to values.
        output_path: Where to save the filled PDF.
        font_size: Font size for text overlay.

    Returns:
        Confirmation message with summary.
    """
    p = _resolve_pdf_path(path)
    if not p.exists():
        return f"PDF not found: {path}"

    if isinstance(fields_json, str):
        try:
            fields_data = json.loads(fields_json)
            fields = fields_data if isinstance(fields_data, list) else fields_data.get("fields", [])
        except json.JSONDecodeError as exc:
            return f"fields_json must be valid JSON: {exc}"
    else:
        fields = fields_json

    if isinstance(values_json, str):
        try:
            values = json.loads(values_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"values_json must be valid JSON: {exc}") from exc
    else:
        values = values_json

    out = Path(output_path) if output_path else p.parent / f"{p.stem}_filled_vision.pdf"

    doc = fitz.open(str(p))
    try:
        filled_count = 0
        skipped = []

        for field in fields:
            label = field.get("label", "")
            if label not in values:
                skipped.append(label)
                continue

            value = str(values[label])
            page_num = int(field.get("page", 1)) - 1
            if page_num < 0 or page_num >= doc.page_count:
                skipped.append(f"{label} (invalid page)")
                continue

            page = doc[page_num]
            bbox = field.get("bbox", [0, 0, 100, 20])
            ftype = field.get("type", "text_input")

            x0, y0, x1, y1 = bbox

            if ftype == "checkbox":
                if value.lower() in ("yes", "true", "x", "1", "checked"):
                    cx = (x0 + x1) / 2
                    cy = (y0 + y1) / 2
                    fs = 9
                    page.insert_text(
                        fitz.Point(cx - 3, cy + fs * 0.35),
                        "X",
                        fontsize=fs,
                        color=(0, 0, 0),
                        fontname="helv",
                    )
                filled_count += 1

            elif ftype == "signature":
                if value and value.lower() not in ("", "n/a", "none"):
                    fs = max(font_size, 12)
                    center_y = (y0 + y1) / 2
                    page.insert_text(
                        fitz.Point(x0 + 2, center_y + fs * 0.35),
                        value,
                        fontsize=fs,
                        color=(0, 0, 0),
                        fontname="helv",
                    )
                filled_count += 1

            else:
                center_y = (y0 + y1) / 2
                page.insert_text(
                    fitz.Point(x0 + 2, center_y + font_size * 0.35),
                    value,
                    fontsize=font_size,
                    color=(0, 0, 0),
                    fontname="helv",
                )
                filled_count += 1

        doc.save(str(out))

    finally:
        doc.close()

    lines = [f"Filled PDF saved to: {out}", f"Fields filled: {filled_count}"]
    if skipped:
        lines.append(
            f"Fields with no matching value ({len(skipped)}): {', '.join(skipped[:20])}"
        )
    return "\n".join(lines)


def vision_update_form_fields(
    path: str,
    fields_json: str | list,
    updates_json: str | dict,
    output_path: str | None = None,
) -> str:
    """Update specific fields in an already-filled vision-processed form."""
    p = _resolve_pdf_path(path)
    out = Path(output_path) if output_path else p.parent / f"{p.stem}_updated_vision.pdf"

    if isinstance(fields_json, str):
        fields_data = json.loads(fields_json)
        fields = fields_data if isinstance(fields_data, list) else fields_data.get("fields", [])
    else:
        fields = fields_json

    if isinstance(updates_json, str):
        updates = json.loads(updates_json)
    else:
        updates = updates_json

    return vision_fill_form_fields(str(p), fields, updates, str(out))
