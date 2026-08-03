"""
Flat PDF form detection and filling for PDFs without AcroForm fields.

Uses a multi-method approach (OCR + drawings + pixel scanning):
1. Tesseract OCR on rendered pages to find labels (handles CIDFont blind spots)
2. page.get_drawings() for underscore lines and checkbox squares
3. Pixel scanning for precise underscore positions

Based on techniques documented in Benoucheca/perso/AGENTS.md.

Usage workflow:
  1. detect_flat_pdf_fields(path) -> JSON with fields, labels, bbox, type
  2. fill_flat_pdf(path, fields_json, values_json, output_path?) -> filled PDF
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

_log = logging.getLogger("flat_pdf_tools")

_DESCENDER_CHARS = set("gjpqy")
_CHECKBOX_SIZE_MIN = 5
_CHECKBOX_SIZE_MAX = 12
_UNDERSCORE_MIN_LEN = 30
_UNDERSCORE_MIN_GAP = 10
_MAX_LABEL_LEN = 60


def _resolve_pdf_path(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    if not p.exists():
        home = os.path.expanduser("~")
        if not str(p).startswith(home) and path_str.startswith("/home/"):
            fallback = Path(home) / "/".join(Path(path_str).parts[3:])
            if fallback.exists():
                return fallback.resolve()
    return p.resolve()


def _has_descender(text: str) -> bool:
    return any(c in _DESCENDER_CHARS for c in text)


def _page_ocr(page: fitz.Page, dpi: int = 200) -> list[dict]:
    """Run Tesseract OCR on a rendered page and return text with bbox coords.

    Uses TSV output mode to get word-level bounding boxes.
    Returns list of dicts: {text, x0, y0, x1, y1}
    Coordinates are in PDF user-space units.
    """
    rect = page.rect
    pix = page.get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tsv_path = tmp.name.replace(".png", ".tsv")
    try:
        img.save(tmp.name)
        subprocess.run(
            ["tesseract", tmp.name, tmp.name.replace(".png", ""), "--psm", "6", "tsv"],
            capture_output=True, text=True, timeout=60,
        )
        if not os.path.exists(tsv_path):
            return []
        with open(tsv_path) as f:
            lines = f.readlines()
    except Exception as e:
        _log.warning("Tesseract TSV failed: %s", e)
        return []
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        if os.path.exists(tsv_path):
            os.unlink(tsv_path)

    scale = rect.width / pix.width

    words = []
    for line in lines[1:]:  # Skip header
        parts = line.strip().split("\t")
        if len(parts) < 12:
            continue
        level = parts[0]
        if level != "5":  # Word level
            continue
        text = parts[11].strip()
        if not text or text == " ":
            continue
        # TSV columns: left, top, width, height (indices 6,7,8,9)
        try:
            left = float(parts[6])
            top = float(parts[7])
            width = float(parts[8])
            height = float(parts[9])
            x0 = left * scale
            y0 = top * scale
            x1 = (left + width) * scale
            y1 = (top + height) * scale
        except (ValueError, IndexError):
            continue
        words.append({
            "text": text,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        })
    return words


def _find_underscores_pixel_scan(page: fitz.Page, dpi: int = 150) -> list[dict]:
    """Find underscore lines by scanning rendered pixels for dark horizontal rows.

    Returns list of {x0, x1, y_bottom, page} in PDF user-space units.
    """
    rect = page.rect
    pix = page.get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    arr = np.array(img)
    h, w = arr.shape[:2]
    scale = rect.width / w

    underscores = []
    DARK_THRESHOLD = 160
    MIN_DARK_FRAC = 0.12

    in_line = False
    line_start_row = 0

    # Handle both RGB (3 channels) and grayscale (2 channels)
    if arr.ndim == 3:
        dark_row_fn = lambda r: np.sum(np.all(arr[r, :, :] < DARK_THRESHOLD, axis=1))
    else:
        dark_row_fn = lambda r: np.sum(arr[r, :] < DARK_THRESHOLD)

    for row in range(h):
        dark = int(dark_row_fn(row))
        dark_frac = dark / w
        is_underscore = dark_frac > MIN_DARK_FRAC and dark > 20

        if is_underscore and not in_line:
            in_line = True
            line_start_row = row
        elif not is_underscore and in_line:
            in_line = False
            line_height = row - line_start_row
            if line_height <= 4:  # underscore lines are thin
                y0_pdf = line_start_row * scale
                y1_pdf = row * scale
                line_pixels = arr[line_start_row:row, :, :] if arr.ndim == 3 else arr[line_start_row:row, :]
                # Find x-extent of dark pixels
                if line_pixels.ndim == 3:
                    dark_cols = np.any(np.all(line_pixels < DARK_THRESHOLD, axis=2), axis=0)
                else:
                    dark_cols = np.any(line_pixels < DARK_THRESHOLD, axis=0)
                if np.any(dark_cols):
                    col_indices = np.where(dark_cols)[0]
                    x0_pdf = col_indices[0] * scale
                    x1_pdf = (col_indices[-1] + 1) * scale
                    if (x1_pdf - x0_pdf) > _UNDERSCORE_MIN_LEN * scale:
                        underscores.append({
                            "x0": round(x0_pdf, 1),
                            "x1": round(x1_pdf, 1),
                            "y_top": round(y0_pdf, 1),
                            "y_bottom": round(y1_pdf, 1),
                        })

    return underscores


def _find_checkboxes_drawings(page: fitz.Page) -> list[dict]:
    """Find checkboxes from drawn line segments forming small squares.

    Returns list of {center_x, center_y, x0, y0, x1, y1} in PDF units.
    """
    segments = []
    for path in page.get_drawings():
        for item in path["items"]:
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                dx = abs(p1.x - p2.x)
                dy = abs(p1.y - p2.y)
                if _CHECKBOX_SIZE_MIN < dx < _CHECKBOX_SIZE_MAX and dy < 2:
                    segments.append({
                        "x0": min(p1.x, p2.x), "y0": min(p1.y, p2.y),
                        "x1": max(p1.x, p2.x), "y1": max(p1.y, p2.y),
                        "orient": "h",
                    })
                elif _CHECKBOX_SIZE_MIN < dy < _CHECKBOX_SIZE_MAX and dx < 2:
                    segments.append({
                        "x0": min(p1.x, p2.x), "y0": min(p1.y, p2.y),
                        "x1": max(p1.x, p2.x), "y1": max(p1.y, p2.y),
                        "orient": "v",
                    })
                elif (_CHECKBOX_SIZE_MIN < dx < _CHECKBOX_SIZE_MAX
                      and _CHECKBOX_SIZE_MIN < dy < _CHECKBOX_SIZE_MAX):
                    segments.append({
                        "x0": min(p1.x, p2.x), "y0": min(p1.y, p2.y),
                        "x1": max(p1.x, p2.x), "y1": max(p1.y, p2.y),
                        "orient": "diag",
                    })

    # Group horizontal + vertical segments into candidate checkbox groups
    h_segs = [s for s in segments if s["orient"] == "h"]
    v_segs = [s for s in segments if s["orient"] == "v"]

    checkboxes = []
    seen_centers = set()

    for hs in h_segs:
        for vs in v_segs:
            # Check if these form a T-junction (one end of h matches v)
            h_center_y = (hs["y0"] + hs["y1"]) / 2
            v_center_x = (vs["x0"] + vs["x1"]) / 2
            h_span = hs["x1"] - hs["x0"]
            v_span = vs["y1"] - vs["y0"]

            if abs(h_span - v_span) > 4:
                continue

            # Check horizontal segment center near vertical segment y-range
            if not (vs["y0"] - 2 <= h_center_y <= vs["y1"] + 2):
                continue
            if not (hs["x0"] - 2 <= v_center_x <= hs["x1"] + 2):
                continue

            cx = round((hs["x0"] + hs["x1"]) / 2, 1)
            cy = round((vs["y0"] + vs["y1"]) / 2, 1)
            key = (round(cx), round(cy))
            if key in seen_centers:
                continue
            seen_centers.add(key)

            checkboxes.append({
                "center_x": cx,
                "center_y": cy,
                "x0": round(hs["x0"], 1),
                "y0": round(vs["y0"], 1),
                "x1": round(hs["x1"], 1),
                "y1": round(vs["y1"], 1),
                "size": round(h_span, 1),
            })

    return checkboxes


def detect_flat_pdf_fields(path: str, pages: str | None = None) -> str:
    """Detect form fields in a flat (non-AcroForm) PDF using OCR + drawings + pixel scanning.

    Args:
        path: Absolute path to the PDF file.
        pages: Page range like "1-3", "1", "1,3,5", or None for all pages.

    Returns:
        JSON string with detected fields, labels, types, and coordinates.

    Raises:
        FileNotFoundError: If the PDF does not exist.
    """
    p = _resolve_pdf_path(path)
    if not p.exists():
        return f"PDF not found: {path}"

    _log.info("Processing %s", p)

    doc = fitz.open(str(p))
    try:
        all_fields = []
        total_pages = doc.page_count

        if pages:
            page_nums = set()
            for part in pages.replace(" ", "").split(","):
                if "-" in part:
                    a, b = part.split("-", 1)
                    page_nums.update(range(int(a), int(b) + 1))
                else:
                    page_nums.add(int(part))
            page_nums = sorted(n for n in page_nums if 1 <= n <= total_pages)
        else:
            page_nums = list(range(1, total_pages + 1))

        for pg_num in page_nums:
            page = doc[pg_num - 1]
            _log.info("Page %d/%d ...", pg_num, total_pages)

            underscores = _find_underscores_pixel_scan(page)
            checkboxes = _find_checkboxes_drawings(page)

            for us in underscores:
                all_fields.append({
                    "type": "text",
                    "page": pg_num,
                    "label": "",
                    "bbox": [round(us["x0"], 1), round(us["y_top"] - 3, 1),
                             round(us["x1"], 1), round(us["y_bottom"] + 2, 1)],
                    "method": "underscore_pixel",
                })

            for cb in checkboxes:
                all_fields.append({
                    "type": "checkbox",
                    "page": pg_num,
                    "label": "",
                    "bbox": [cb["x0"], cb["y0"], cb["x1"], cb["y1"]],
                    "center": [cb["center_x"], cb["center_y"]],
                    "method": "drawing",
                })

        labeled = _label_fields(all_fields, doc, page_nums)

    finally:
        doc.close()

    return json.dumps({
        "fields": labeled,
        "total": len(labeled),
        "pages_processed": page_nums,
        "note": "Labels are heuristics; verify against the actual form. "
                "Fields with empty labels need manual naming.",
    }, indent=2, ensure_ascii=False)


def _normalize_label(text: str) -> str:
    """Normalize unicode, strip leading markers, clean whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = text.lstrip("*\u2022\u2023\u25E6\u2043")  # Remove leading markers (*, •, ‣, ◦, ⁃)
    return text.strip()


def _label_fields(fields: list[dict], doc: fitz.Document, page_nums: list[int]) -> list[dict]:
    """Label detected fields using native text + OCR with positions.

    Tries in order:
    1. Native text blocks (above field, then same-line)
    2. OCR word-level positions (above field, then same-line)
    Falls back to auto-generated pageN_fieldM names.

    Handles CIDFont-hidden labels via OCR.
    """
    # Pre-compute OCR words for each page
    ocr_words_by_page = {}
    for pg_num in set(f["page"] for f in fields):
        if pg_num - 1 < doc.page_count:
            page = doc[pg_num - 1]
            ocr_words_by_page[pg_num] = _page_ocr(page)

    field_index = 0
    label_count: dict[str, int] = {}
    labeled = []
    for f in fields:
        page_num = f["page"]
        if page_num - 1 >= doc.page_count:
            labeled.append(f)
            continue
        page = doc[page_num - 1]
        ocr_words = ocr_words_by_page.get(page_num, [])

        label = ""
        if f["type"] in ("text", "signature", "date"):
            # Prefer OCR (handles CIDFonts). Falls back to native text, then same-line.
            label = _find_ocr_label_near_y(ocr_words, f["bbox"][3], f["bbox"][0])
            if not label:
                label = _find_label_near_y(page, f["bbox"][3], f["bbox"][0], max_dist=55)
            if not label:
                label = _find_ocr_label_same_line(ocr_words, f["bbox"][3], f["bbox"][0],
                                                  f["bbox"][1], f["bbox"][3])
            if not label:
                label = _find_label_same_line(page, f["bbox"], f["bbox"][0])
        elif f["type"] == "checkbox":
            label = _find_label_near_y(page, f["center"][1], f["bbox"][0] - 100, max_dist=55)
            if not label:
                label = _find_label_same_line(page, f["bbox"], f["bbox"][0] - 100)
            if not label:
                label = _find_ocr_label_near_y(ocr_words, f["center"][1], f["bbox"][0] - 100)
            if label:
                f["label"] = label

        if label:
            # Skip section headers (all-caps, short, no fill keyword)
            is_section_header = (
                len(label.split()) <= 3
                and label.isupper()
                and not any(w in label.lower() for w in ['name', 'date', 'phone', 'email', 'address', 'city', 'state', 'zip', 'birth', 'age', 'sign'])
            )
            if not is_section_header:
                f["label"] = label
                if re.search(r"\bsign(ature)?\b", label, re.IGNORECASE):
                    f["type"] = "signature"
                elif re.search(r"\bdate\b", label, re.IGNORECASE):
                    f["type"] = "date"
            else:
                label = ""  # Clear so fallback name is used

        if not f["label"]:
            field_index += 1
            f["label"] = f"page{f['page']}_field{field_index}"

        # Deduplicate labels (e.g. two "Signature-(Self) Date" fields)
        if f["label"] in label_count:
            label_count[f["label"]] += 1
            f["label"] = f"{f['label']}_{label_count[f['label']]}"
        else:
            label_count[f["label"]] = 1

        labeled.append(f)
    return labeled


def _find_ocr_label_near_y(ocr_words: list[dict], target_y: float, max_x: float,
                           max_dist: float = 55) -> str:
    """Find OCR-detected text label near target_y within x-range.

    Tries TWO strategies, returning the closest match:
    1. ABOVE target_y (typical: label above underscore)
    2. NEAR target_y (label below or on same visual line as underscore)
    For each, picks the closest vertical match, then groups words on that line.

    Handles CIDFont-hidden text and labels placed slightly below the underscore line.
    """
    best_label = ""
    best_dist = 999

    # Strategy 1: above target_y
    words_above = [
        w for w in ocr_words
        if w["y1"] < target_y and w["x0"] < max_x + 60
        and (target_y - w["y1"]) < max_dist
        and w["x0"] > 30
    ]
    if words_above:
        words_above.sort(key=lambda w: (-w["y1"], w["x0"]))
        closest = max(w["y1"] for w in words_above)
        dist = target_y - closest
        same = [w for w in words_above if abs(w["y1"] - closest) < 4]
        same.sort(key=lambda w: w["x0"])
        label = " ".join(w["text"] for w in same).strip()
        if 2 <= len(label) <= _MAX_LABEL_LEN:
            best_label = label
            best_dist = dist

    # Strategy 2: near/at target_y (label on same line or slightly below)
    words_near = [
        w for w in ocr_words
        if w["x0"] < max_x + 60 and w["x0"] > 30
        and target_y - 8 <= w["y0"] <= target_y + 25
    ]
    if words_near:
        words_near.sort(key=lambda w: (abs(w["y0"] - target_y), w["x0"]))
        # Find the line closest to target_y
        line_candidates = {}
        for w in words_near:
            dist = abs(w["y0"] - target_y)
            key = round(w["y0"])
            if key not in line_candidates or dist < line_candidates[key][0]:
                line_candidates[key] = (dist, w["y0"], w["text"])
        # Pick the line with smallest distance
        if line_candidates:
            best_key = min(line_candidates, key=lambda k: line_candidates[k][0])
            dist, best_y0, _ = line_candidates[best_key]
            same = [w for w in words_near if abs(w["y0"] - best_y0) < 5]
            same.sort(key=lambda w: w["x0"])
            label = " ".join(w["text"] for w in same).strip()
            if 2 <= len(label) <= _MAX_LABEL_LEN and dist < best_dist:
                best_label = label
                best_dist = dist

    return best_label


def _find_ocr_label_same_line(ocr_words: list[dict], target_y: float, target_x: float,
                              field_y0: float, field_y1: float) -> str:
    """Find OCR text on the same line as the field, to the left of it.

    Used for CIDFont-hidden labels placed on the same line as underscores.
    """
    same_line = [
        w for w in ocr_words
        if w["x1"] < target_x and w["x0"] < target_x
        and w["y0"] < target_y and w["y1"] >= field_y0 - 5
        and field_y0 - 10 <= w["y1"] <= field_y1 + 10
    ]
    if not same_line:
        return ""
    same_line.sort(key=lambda w: w["x0"])
    label = " ".join(w["text"] for w in same_line).strip()
    if len(label) < 2 or len(label) > _MAX_LABEL_LEN:
        return ""
    return label.rstrip(":").strip()


def _find_label_same_line(page: fitz.Page, field_bbox: list, max_x: float) -> str:
    """Find text on the same line as a field, to the left of the underscore.

    Used for labels like 'I, ____________' where the label is on the same line.
    """
    field_y0, field_y1 = field_bbox[1], field_bbox[3]

    blocks = page.get_text("dict", sort=True)["blocks"]
    for block in blocks:
        if block.get("type") != 0:
            continue
        block_text = ""
        block_x1 = 0
        block_x0 = 9999
        block_top = 0
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bbox = span["bbox"]
                t = span["text"].strip()
                if not t:
                    continue
                block_text += t + " "
                block_x1 = max(block_x1, bbox[1])
                block_x0 = min(block_x0, bbox[0])
                block_top = span["origin"].y if hasattr(span["origin"], "y") else bbox[1]

        block_text = _normalize_label(block_text)
        if not block_text or len(block_text) < 2 or len(block_text) > _MAX_LABEL_LEN:
            continue

        # Same y-range: block vertically overlaps with the field
        block_bottom = block.get("bbox", [0, 0, 0, 0])[3]
        overlap = min(field_y1, block_bottom) - max(field_y0, block_top)
        if overlap < 3:
            continue  # Not on same line
        if block_x0 > max_x:
            continue  # Block is to the right of the field
        if "___" in block_text or block_text.count("_") > 5:
            continue  # Skip if the block is mostly underscores

        return block_text.rstrip(":").strip()

    return ""


def _find_label_near_y(page: fitz.Page, target_y: float, max_x: float, max_dist: float = 55) -> str:
    """Find the nearest text label above target_y within x-range.

    Filters out:
    - Long text (instructions, paragraphs >60 chars)
    - Text that is too far above (>55pt gap suggests different section)
    - Short fragments (<2 chars)
    """
    best_label = ""
    best_dist = 999

    blocks = page.get_text("dict", sort=True)["blocks"]
    for block in blocks:
        if block.get("type") != 0:
            continue
        # Get the full text of the block
        block_text = ""
        block_bottom = 0
        block_x0 = 9999
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bbox = span["bbox"]
                t = span["text"].strip()
                if not t:
                    continue
                block_text += t + " "
                block_bottom = max(block_bottom, bbox[3])
                block_x0 = min(block_x0, bbox[0])

        block_text = _normalize_label(block_text)

        if not block_text or len(block_text) < 2:
            continue
        if len(block_text) > _MAX_LABEL_LEN:
            continue  # Skip instructions/paragraphs
        if block_bottom >= target_y:
            continue  # Not above the field
        if block_x0 > max_x + 60:
            continue  # Too far right

        dist = target_y - block_bottom
        if 0 < dist < best_dist and dist < max_dist:
            best_dist = dist
            best_label = block_text

    return best_label


def fill_flat_pdf(
    path: str,
    fields_json: str | list,
    values_json: str | dict,
    output_path: str | None = None,
) -> str:
    """Fill a flat PDF form using detected field coordinates.

    Args:
        path: Absolute path to source PDF.
        fields_json: JSON string or list from detect_flat_pdf_fields.
        values_json: JSON string or dict mapping field labels/names to values.
            For signature fields, value can be an image path (PNG) or text.
        output_path: Output path. Defaults to {stem}_filled_flat.pdf.

    Returns:
        Confirmation message with filled field names and any skipped fields.
    """
    p = _resolve_pdf_path(path)
    if not p.exists():
        return f"PDF not found: {path}"

    if isinstance(fields_json, str):
        try:
            data = json.loads(fields_json)
            fields = data if isinstance(data, list) else data.get("fields", [])
        except json.JSONDecodeError as exc:
            return f"fields_json must be valid JSON: {exc}"
    else:
        fields = fields_json

    if isinstance(values_json, str):
        try:
            values = json.loads(values_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"values_json must be valid JSON: {exc}")
    else:
        values = values_json

    out = Path(output_path) if output_path else p.parent / f"{p.stem}_filled_flat.pdf"

    doc = fitz.open(str(p))
    try:
        filled_count = 0
        skipped = []
        # Track what we've placed to avoid overlaps
        placed_bboxes = []

        for field in fields:
            label = field.get("label", "")
            ftype = field.get("type", "text")
            page_num = int(field.get("page", 1)) - 1

            if label not in values:
                skipped.append(label or f"page{page_num+1}_unnamed")
                continue

            value = str(values[label])
            if not value or value.lower() in ("n/a", ""):
                filled_count += 1
                continue

            if page_num < 0 or page_num >= doc.page_count:
                skipped.append(f"{label} (invalid page)")
                continue

            page = doc[page_num]
            bbox = field.get("bbox", [0, 0, 100, 20])
            x0, y0, x1, y1 = bbox

            if ftype == "checkbox":
                center = field.get("center", [(x0 + x1) / 2, (y0 + y1) / 2])
                cx, cy = center[0], center[1]
                if value.lower() in ("yes", "true", "x", "1", "checked", "on"):
                    page.insert_text(
                        fitz.Point(cx - 3, cy + 3.5),
                        "X", fontsize=10, color=(0, 0, 0), fontname="helv",
                    )
                filled_count += 1

            elif ftype == "signature":
                # Check if value is a file path to an image
                sig_path = _resolve_pdf_path(value)
                if sig_path.exists() and sig_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif"):
                    sig_rect = fitz.Rect(x0, y0, x1, y1)
                    page.insert_image(sig_rect, filename=str(sig_path), keep_proportion=True, overlay=True)
                else:
                    # Render as text
                    text_baseline = _compute_baseline(y1, value)
                    page.insert_text(
                        fitz.Point(x0 + 2, text_baseline),
                        value, fontsize=12, color=(0, 0, 0), fontname="helv",
                    )
                filled_count += 1

            else:
                # Text / date / number field
                text_baseline = _compute_baseline(y1, value)
                page.insert_text(
                    fitz.Point(x0 + 2, text_baseline),
                    value, fontsize=12, color=(0, 0, 0), fontname="helv",
                )
                filled_count += 1

        doc.save(str(out))

    finally:
        doc.close()

    lines = [f"Filled PDF saved to: {out}", f"Fields filled: {filled_count}"]
    if skipped:
        lines.append(
            f"No matching value for ({len(skipped)}): {', '.join(skipped[:20])}"
        )
    return "\n".join(lines)


def _compute_baseline(bbox_bottom: float, text: str) -> float:
    """Compute the text insertion baseline from bbox bottom with descender awareness.

    Per Benoucheca formula:
      - No descender (no g/j/p/q/y): baseline = bbox_bottom - 3
      - Has descender (g, j, p, q, y): baseline = bbox_bottom - 6
    """
    if _has_descender(text):
        return bbox_bottom - 6
    return bbox_bottom - 3


DETECT_FLAT_PDF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "detect_flat_pdf_fields",
        "description": (
            "Detect form fields in a flat PDF (no AcroForm fields) using OCR + pixel scanning + drawings analysis. "
            "Handles CIDFont-hidden labels that get_text() misses. "
            "Returns JSON with field types (text/checkbox/signature/date), page numbers, "
            "bounding boxes, and heuristic labels. Use before fill_flat_pdf. "
            "Best for scanned forms or print-to-PDF forms with underscore lines and checkboxes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the PDF file"},
                "pages": {
                    "type": "string",
                    "description": "Page range like '1-5', '3', or '1,3,5'. Defaults to all pages.",
                    "default": "",
                },
            },
            "required": ["path"],
        },
    },
}

FILL_FLAT_PDF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fill_flat_pdf",
        "description": (
            "Fill fields in a flat PDF using coordinates from detect_flat_pdf_fields. "
            "Overlays text at computed baselines (descender-aware per Benoucheca formula). "
            "Handles text inputs, checkboxes, signatures (text or image path), and date fields. "
            "Use after detect_flat_pdf_fields to fill the detected fields."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to source PDF"},
                "fields_json": {
                    "type": "string",
                    "description": (
                        'JSON from detect_flat_pdf_fields: {"fields": [...], "total": N} '
                        "or the raw fields array"
                    ),
                },
                "values_json": {
                    "type": "string",
                    "description": (
                        'JSON object mapping field labels to values: '
                        '{"Full Name": "John Doe", "Signature": "/path/to/sig.png", ...}. '
                        "For checkboxes use 'Yes' or 'No'. "
                        "For signature fields, value can be an image path (PNG) or text."
                    ),
                },
                "output_path": {
                    "type": "string",
                    "description": "Output path (defaults to {stem}_filled_flat.pdf)",
                    "default": "",
                },
            },
            "required": ["path", "fields_json", "values_json"],
        },
    },
}
