"""Document quality gates for generated Office/PDF files.

Patterns ported fresh (no code lifted) from the techniques studied in
OpenCoworkAI/open-cowork (MIT) and thvroyal/kimi-skills (unlicensed — pattern
reference only):

- kimi-xlsx "recheck/reference-check": formula-error scan, forbidden-function
  gate, suspicious small-range references.
- kimi-docx `element_order.py`: stable-sort OOXML child-element ordering so
  AI-generated document.xml opens in Word instead of "unreadable content".
- open-cowork pack gate + kimi-pdf post-render gate: a hard check that runs
  before a generated document is delivered.

Every function returns a JSON string (the executor contract used by
tools/registry.py) so results slot directly into the tool result channel.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from lxml import etree

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_XLSX_ERROR_STRINGS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NULL!", "#NUM!")

# Functions that break Excel <= 2019 / LibreOffice < 7.2 and so must not ship
# in a generated workbook. Regex-agnostic token list.
_FORBIDDEN_FUNCTIONS = (
    "FILTER", "UNIQUE", "XLOOKUP", "LET", "LAMBDA", "RANDARRAY",
    "SORT", "SORTBY", "SEQUENCE", "TEXTSPLIT", "TEXTAFTER", "TEXTBEFORE",
    "TOCOL", "TOROW", "TAKE", "DROP", "HSTACK", "VSTACK", "CHOOSECOLS", "CHOOSEROWS",
)

# Aggregates over a range of <= 2 cells are almost always an off-by-one from
# 0-indexed pandas code — the dominant AI-generated-XLSX failure mode.
_SMALL_AGG = re.compile(r"=(SUM|AVERAGE|MIN|MAX|MEDIAN)\(([A-Z]{1,3})(\d+):([A-Z]{1,3})(\d+)\)", re.I)

_OXML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _localname(el) -> str:
    return etree.QName(el).localname


# ---------------------------------------------------------------------------
# OOXML child-element ordering (kimi-docx element_order.py pattern)
# ---------------------------------------------------------------------------

# Official schema order for the common container elements. Unknown elements
# are preserved at the end in their original relative order (lossless).
_RPR_ORDER = [
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike",
    "dstrike", "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid",
    "vanish", "webHidden", "color", "spacing", "w", "kern", "position", "sz",
    "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign",
    "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath",
]

_PPR_ORDER = [
    "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl",
    "numPr", "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens",
    "kinsoku", "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE",
    "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid", "spacing", "ind",
    "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc", "textDirection",
    "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle",
    "rPr", "sectPr", "pPrChange",
]

_SECTPR_ORDER = [
    "footnotePr", "endnotePr", "type", "pgSz", "pgMar", "paperSrc", "pgBorders",
    "lnNumType", "pgNumType", "cols", "formProt", "vAlign", "noEndnote", "titlePg",
    "textDirection", "bidi", "rtlGutter", "docGrid", "printerSettings", "sectPrChange",
]

_TCPR_ORDER = [
    "cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd",
    "noWrap", "tcMar", "textDirection", "tcFitText", "vAlign", "hideMark",
    "headers", "cellIns", "cellDel", "cellMerge", "tcPrChange",
]

_TBLPR_ORDER = [
    "tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize",
    "tblStyleColBandSize", "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders",
    "shd", "tblLayout", "tblCellMar", "tblLook", "tblCaption", "tblDescription",
    "tblPrChange",
]

_BORDERS_ORDER = ["top", "left", "bottom", "right", "insideH", "insideV", "tl2br", "tr2bl"]

_LEVEL_ORDER = [
    "start", "numFmt", "lvlRestart", "pStyle", "isLgl", "suff", "lvlText", "lvlPicBulletId",
    "legacy", "lvlJc", "pPr", "rPr",
]

_REORDER_TARGETS = {
    "rPr": _RPR_ORDER,
    "pPr": _PPR_ORDER,
    "sectPr": _SECTPR_ORDER,
    "tcPr": _TCPR_ORDER,
    "tblPr": _TBLPR_ORDER,
    "tblBorders": _BORDERS_ORDER,
    "tcBorders": _BORDERS_ORDER,
    "level": _LEVEL_ORDER,
}


def _reorder_children(parent, order: list[str]) -> bool:
    """Stable-sort `parent`'s children so known names follow schema order and
    unknown names keep their original relative order at the end. Returns True
    if the child sequence changed."""
    idx = {name: i for i, name in enumerate(order)}

    def key(el):
        ln = _localname(el)
        if ln in idx:
            return (0, idx[ln])
        return (1, 0)

    children = list(parent)
    ordered = sorted(children, key=key)
    if [id(c) for c in children] == [id(c) for c in ordered]:
        return False
    for c in ordered:
        parent.remove(c)
    for c in ordered:
        parent.append(c)
    return True


def _fix_body_sectpr(body) -> bool:
    """sectPr must be the LAST child of w:body per the schema; AI-generated
    files frequently bury it mid-body."""
    sect = None
    for child in list(body):
        if _localname(child) == "sectPr":
            sect = child
            break
    if sect is None:
        return False
    if list(body)[-1] is sect:
        return False
    body.remove(sect)
    body.append(sect)
    return True


def _xml_repair_docx_part(raw: bytes) -> tuple[bytes, list[str], bool]:
    """Reorder OOXML children in a document.xml part. Returns
    (new_xml, change_log, ok). Raises ValueError on unparseable XML."""
    parser = etree.XMLParser(remove_blank_text=False)
    try:
        root = etree.fromstring(raw, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"document.xml is not well-formed XML: {exc}")

    log: list[str] = []
    changed = False

    def visit(el):
        nonlocal changed
        for child in list(el):
            visit(child)
        ln = _localname(el)
        order = _REORDER_TARGETS.get(ln)
        if order is not None and _reorder_children(el, order):
            changed = True
            log.append(f"reordered children of <w:{ln}>")

    visit(root)
    body = root.find(f"{{{_OXML_NS}}}body")
    if body is not None and _fix_body_sectpr(body):
        changed = True
        log.append("moved <w:sectPr> to end of <w:body>")

    out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return out, log, changed


def _read_zip_entries(path: Path) -> list[tuple[zipfile.ZipInfo, bytes]]:
    with zipfile.ZipFile(path) as zf:
        return [(info, zf.read(info.filename)) for info in zf.infolist()]


def _write_zip_entries(path: Path, entries: list[tuple[zipfile.ZipInfo, bytes]]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for info, data in entries:
            zf.writestr(info, data)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def repair_docx_element_order(path: str) -> str:
    """Repair OOXML child-element ordering in a .docx in place so Word can open it.

    Fixes mis-ordered rPr/pPr/tcPr/tblPr/sectPr children and a w:sectPr that
    is not last in the body. Unknown elements are preserved. Returns a JSON
    report.
    """
    p = Path(path)
    if not p.exists():
        return json.dumps({"ok": False, "error": f"file not found: {path}"})

    try:
        entries = _read_zip_entries(p)
    except (zipfile.BadZipFile, OSError) as exc:
        return json.dumps({"ok": False, "error": f"not a valid zip: {exc}"})

    parts = {info.filename: data for info, data in entries}
    doc_part = "word/document.xml"
    if doc_part not in parts:
        return json.dumps({"ok": False, "error": f"missing {doc_part} — not a .docx"})

    try:
        new_xml, log, changed = _xml_repair_docx_part(parts[doc_part])
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)})

    if not changed:
        return json.dumps({"ok": True, "repaired": False, "changes": [], "path": str(p)})

    # Rebuild the zip with the repaired part, preserving every other entry.
    out = []
    for info, data in entries:
        if info.filename == doc_part:
            out.append((info, new_xml))
        else:
            out.append((info, data))
    try:
        _write_zip_entries(p, out)
    except OSError as exc:
        return json.dumps({"ok": False, "error": f"write failed: {exc}"})

    return json.dumps({"ok": True, "repaired": True, "changes": log, "path": str(p)})


def validate_docx(path: str) -> str:
    """Structural validation of a .docx before delivery.

    Checks: zip integrity, required parts present, document.xml parses,
    w:sectPr is last in body, and internal relationship targets resolve.
    Returns a JSON report with an `issues` list (empty = clean).
    """
    p = Path(path)
    if not p.exists():
        return json.dumps({"ok": False, "error": f"file not found: {path}"})

    issues: list[str] = []
    try:
        entries = {info.filename: data for info, data in _read_zip_entries(p)}
    except (zipfile.BadZipFile, OSError) as exc:
        return json.dumps({"ok": False, "error": f"not a valid zip: {exc}"})

    for required in ("[Content_Types].xml", "word/document.xml", "_rels/.rels"):
        if required not in entries:
            issues.append(f"missing required part: {required}")

    if "word/document.xml" not in entries:
        return json.dumps({"ok": len(issues) == 0, "issues": issues, "path": str(p)})

    try:
        root = etree.fromstring(entries["word/document.xml"])
    except etree.XMLSyntaxError as exc:
        issues.append(f"word/document.xml does not parse: {exc}")
        return json.dumps({"ok": False, "issues": issues, "path": str(p)})

    body = root.find(f"{{{_OXML_NS}}}body")
    if body is None:
        issues.append("no w:body in document.xml")
    else:
        sect = [c for c in body if _localname(c) == "sectPr"]
        if sect and list(body)[-1] is not sect[-1]:
            issues.append("w:sectPr is not the last element of w:body (run repair_docx_element_order)")

    # Internal relationship targets must exist (skip External ones).
    rels_part = "word/_rels/document.xml.rels"
    if rels_part in entries:
        try:
            rel_root = etree.fromstring(entries[rels_part])
        except etree.XMLSyntaxError:
            rel_root = None
        if rel_root is not None:
            from posixpath import normpath

            ns = {"r": _PKG_NS}
            for rel in rel_root.findall(".//r:Relationship", ns):
                target = rel.get("Target", "")
                mode = rel.get("TargetMode", "Internal")
                if mode == "External" or not target or target.startswith("/"):
                    continue
                # A relative Target in document.xml.rels is relative to word/.
                key = normpath(f"word/{target}")
                if key not in entries:
                    issues.append(f"broken relationship: {rel.get('Id')} -> {target}")

    return json.dumps({"ok": len(issues) == 0, "issues": issues, "path": str(p)})


# ---------------------------------------------------------------------------
# DOCX comments (kimi-docx comments.py pattern — minimal Word-valid wiring)
# ---------------------------------------------------------------------------

_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_COMMENTS_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
_COMMENTS_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"


def _paragraph_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(f"{{{_OXML_NS}}}t"))


def _comment_id_from_parts(parts: dict[str, bytes]) -> int:
    raw = parts.get("word/comments.xml", b"")
    if not raw:
        return 0
    try:
        root = etree.fromstring(raw)
    except etree.XMLSyntaxError:
        return 0
    ids = [int(c.get(f"{{{_OXML_NS}}}id") or 0) for c in root.iter(f"{{{_OXML_NS}}}comment")]
    return (max(ids) + 1) if ids else 0


def add_docx_comment(path: str, target_text: str, comment_text: str, author: str = "Clixen") -> str:
    """Insert a Word comment anchored to the first occurrence of `target_text`.

    Wires the minimal Word-valid set: the comment lives in word/comments.xml,
    the anchor (commentRangeStart/End + commentReference run) in document.xml,
    plus the relationship and [Content_Types] override. Modern Word writes
    three more parts (commentsExtended/Ids/Extensible) but they're optional —
    Word opens and displays this 2-part form fine. Returns a JSON report.
    """
    from datetime import datetime, timezone

    p = Path(path)
    if not p.exists():
        return json.dumps({"ok": False, "error": f"file not found: {path}"})

    try:
        entries = _read_zip_entries(p)
    except (zipfile.BadZipFile, OSError) as exc:
        return json.dumps({"ok": False, "error": f"not a valid zip: {exc}"})

    parts = {info.filename: data for info, data in entries}
    try:
        root = etree.fromstring(parts["word/document.xml"])
    except (KeyError, etree.XMLSyntaxError) as exc:
        return json.dumps({"ok": False, "error": f"cannot parse document.xml: {exc}"})

    target_lower = target_text.lower()
    match_par = None
    target_offset = 0
    for par in root.iter(f"{{{_OXML_NS}}}p"):
        text = _paragraph_text(par)
        idx = text.lower().find(target_lower)
        if idx != -1:
            match_par = par
            target_offset = idx
            break
    if match_par is None:
        return json.dumps({"ok": False, "error": f"target text not found: {target_text!r}"})

    # Locate the run range covering [target_offset, target_offset+len(target)).
    runs = [c for c in match_par if _localname(c) == "r"]
    run_texts = [(_paragraph_text(c), c) for c in runs]
    t_len = len(target_text)
    start_idx = end_idx = None
    cur = 0
    for i, (txt, _run) in enumerate(run_texts):
        r_end = cur + len(txt)
        if start_idx is None and r_end > target_offset:
            start_idx = i
        if r_end >= target_offset + t_len:
            end_idx = i
            break
        cur = r_end
    if start_idx is None or end_idx is None:
        return json.dumps({"ok": False, "error": "target spans unsupported run layout"})

    comment_id = _comment_id_from_parts(parts)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    comment_xml = (
        f'<w:comment xmlns:w="{_OXML_NS}" w:id="{comment_id}" w:author="{author}" w:date="{date_str}">'
        f"<w:p><w:r><w:rPr><w:rStyle w:val=\"CommentReference\"/></w:rPr><w:annotationRef/></w:r>"
        f"<w:r><w:rPr><w:rStyle w:val=\"CommentText\"/></w:rPr><w:t xml:space=\"preserve\">"
        f"{comment_text}</w:t></w:r></w:p></w:comment>"
    )

    # -- document.xml: insert anchor elements around the target run range.
    children = list(match_par)
    s = children.index(run_texts[start_idx][1])
    e = children.index(run_texts[end_idx][1])
    range_start = etree.fromstring(f'<w:commentRangeStart xmlns:w="{_OXML_NS}" w:id="{comment_id}"/>')
    range_end = etree.fromstring(f'<w:commentRangeEnd xmlns:w="{_OXML_NS}" w:id="{comment_id}"/>')
    ref_run = etree.fromstring(
        f'<w:r xmlns:w="{_OXML_NS}"><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr>'
        f'<w:commentReference w:id="{comment_id}"/></w:r>'
    )
    match_par.insert(s, range_start)
    match_par.insert(e + 2, range_end)      # end_idx shifted +1 by the start insert
    match_par.insert(e + 3, ref_run)
    parts["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    # -- comments.xml: create (with rel + content-type override) or append.
    if "word/comments.xml" not in parts:
        comments_root = etree.fromstring(f'<w:comments xmlns:w="{_OXML_NS}"/>')
        ct_root = etree.fromstring(parts["[Content_Types].xml"])
        override = etree.fromstring(
            f'<Override xmlns="{_CT_NS}" PartName="/word/comments.xml" ContentType="{_COMMENTS_CT}"/>'
        )
        ct_root.append(override)
        parts["[Content_Types].xml"] = etree.tostring(ct_root, xml_declaration=True, encoding="UTF-8", standalone=True)
        rels_root = etree.fromstring(parts["word/_rels/document.xml.rels"])
        rel = etree.fromstring(
            f'<Relationship xmlns="{_PKG_NS}" Id="rIdComments" '
            f'Type="{_COMMENTS_REL_TYPE}" Target="comments.xml"/>'
        )
        rels_root.append(rel)
        parts["word/_rels/document.xml.rels"] = etree.tostring(rels_root, xml_declaration=True, encoding="UTF-8", standalone=True)
    else:
        comments_root = etree.fromstring(parts["word/comments.xml"])
    comments_root.append(etree.fromstring(comment_xml))
    parts["word/comments.xml"] = etree.tostring(comments_root, xml_declaration=True, encoding="UTF-8", standalone=True)

    out = []
    written = set()
    for info, data in entries:
        written.add(info.filename)
        if info.filename in parts:
            out.append((info, parts[info.filename]))
        else:
            out.append((info, data))
    for name, data in parts.items():
        if name not in written:
            out.append((zipfile.ZipInfo(name), data))
    try:
        _write_zip_entries(p, out)
    except OSError as exc:
        return json.dumps({"ok": False, "error": f"write failed: {exc}"})

    return json.dumps({
        "ok": True, "comment_id": comment_id, "author": author,
        "paragraph_index": list(root.iter(f"{{{_OXML_NS}}}p")).index(match_par),
        "target_text": target_text, "path": str(p),
    })


def _count_range_cells(match) -> int:
    _, c1, r1, c2, r2 = match.groups()
    col1, col2 = ord(c1[0]) - 64 + (len(c1) - 1) * 26, ord(c2[0]) - 64 + (len(c2) - 1) * 26
    return (col2 - col1 + 1) * (int(r2) - int(r1) + 1)


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

def check_xlsx_quality(path: str) -> str:
    """Quality gate for a generated .xlsx (kimi-xlsx recheck/reference-check).

    Scans every cell for Excel error strings, flags forbidden dynamic-array
    functions (break Excel <= 2019), and flags aggregates over ranges of 1-2
    cells (the pandas off-by-one signature). Returns a JSON report.
    """
    import openpyxl

    p = Path(path)
    if not p.exists():
        return json.dumps({"ok": False, "error": f"file not found: {path}"})

    try:
        wb_formulas = openpyxl.load_workbook(p, data_only=False)
        wb_values = openpyxl.load_workbook(p, data_only=True)
    except Exception as exc:  # noqa: BLE001 — corrupt workbook, report it
        return json.dumps({"ok": False, "error": f"openpyxl failed to load workbook: {exc}"})

    error_locs: dict[str, list[str]] = {e: [] for e in _XLSX_ERROR_STRINGS}
    forbidden_locs: list[str] = []
    small_agg_locs: list[str] = []
    total_formulas = 0
    total_cells = 0

    for ws_f, ws_v in zip(wb_formulas.worksheets, wb_values.worksheets):
        for row in ws_f.iter_rows():
            for cell in row:
                total_cells += 1
                formula = cell.value if isinstance(cell.value, str) and cell.value.startswith("=") else None
                cached = ws_v[cell.coordinate].value
                for err in _XLSX_ERROR_STRINGS:
                    if (isinstance(cached, str) and err in cached) or (formula and err in formula):
                        error_locs[err].append(cell.coordinate)
                if formula:
                    total_formulas += 1
                    up = formula.upper()
                    for fn in _FORBIDDEN_FUNCTIONS:
                        # Match a function token, not a substring (e.g. UNIQUE inside a string).
                        if re.search(rf"\b{fn}\s*\(", up):
                            forbidden_locs.append(f"{cell.coordinate}: {fn}")
                    for m in _SMALL_AGG.finditer(up):
                        if _count_range_cells(m) <= 2:
                            small_agg_locs.append(f"{cell.coordinate}: {m.group(1)}({m.group(2)}{m.group(3)}:{m.group(4)}{m.group(5)})")

    error_summary = {k: v for k, v in error_locs.items() if v}
    ok = not error_summary and not forbidden_locs and not small_agg_locs
    return json.dumps({
        "ok": ok,
        "status": "ok" if ok else "errors_found",
        "total_formulas": total_formulas,
        "total_cells": total_cells,
        "formula_errors": {k: {"count": len(v), "locations": v[:20]} for k, v in error_summary.items()},
        "forbidden_functions": forbidden_locs[:20],
        "suspicious_small_aggregates": small_agg_locs[:20],
        "path": str(p),
    })


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def check_pdf_anomalies(path: str, min_chars: int = 20) -> str:
    """Post-render anomaly scan for a generated PDF (kimi-pdf gate).

    Flags blank and low-content pages via text extraction. Returns a JSON
    report with the page numbers to inspect.
    """
    import pypdfium2 as pdfium

    p = Path(path)
    if not p.exists():
        return json.dumps({"ok": False, "error": f"file not found: {path}"})

    try:
        doc = pdfium.PdfDocument(str(p))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"failed to open pdf: {exc}"})

    blank: list[int] = []
    low: list[int] = []
    page_count = len(doc)
    for i in range(page_count):
        try:
            page = doc[i]
            tp = page.get_textpage()
            text = (tp.get_text_range() or "").strip()
        except Exception:  # noqa: BLE001 — unreadable page counts as an anomaly
            text = ""
        if not text:
            blank.append(i + 1)
        elif len(text) < min_chars:
            low.append(i + 1)

    ok = not blank and not low
    return json.dumps({
        "ok": ok,
        "page_count": page_count,
        "blank_pages": blank,
        "low_content_pages": low,
        "min_chars_threshold": min_chars,
        "path": str(p),
    })


# ---------------------------------------------------------------------------
# Tool schemas + executors (registry contract: flat args, JSON-string result)
# ---------------------------------------------------------------------------

VALIDATE_DOCX_SCHEMA = {
    "type": "function",
    "function": {
        "name": "validate_docx",
        "description": (
            "Validate a Word (.docx) file for structural corruption before delivery: "
            "zip integrity, required parts, XML parses, w:sectPr ordering, relationship targets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the .docx file"},
            },
            "required": ["path"],
        },
    },
}

REPAIR_DOCX_ELEMENT_ORDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "repair_docx_element_order",
        "description": (
            "Repair a .docx whose OOXML child elements are out of schema order (common in "
            "AI-generated files that Word rejects with 'unreadable content'). Reorders "
            "rPr/pPr/tcPr/tblPr/sectPr children and moves w:sectPr to the end of the body. "
            "Rewrites the file in place."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the .docx file to repair in place"},
            },
            "required": ["path"],
        },
    },
}

CHECK_XLSX_QUALITY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_xlsx_quality",
        "description": (
            "Quality gate for a generated Excel (.xlsx) file: scans every cell for formula errors "
            "(#REF!, #DIV/0!, #VALUE!, #NAME?, #N/A, etc.), flags functions that break Excel <= 2019 "
            "(FILTER, UNIQUE, XLOOKUP, LET, LAMBDA, dynamic arrays), and flags SUM/AVERAGE over "
            "1-2 cell ranges (off-by-one signature). Returns a JSON report."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the .xlsx file"},
            },
            "required": ["path"],
        },
    },
}

ADD_DOCX_COMMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "add_docx_comment",
        "description": (
            "Insert a Word comment into a .docx anchored to the first occurrence of a target "
            "text phrase. Wires the minimal Word-valid comment parts (comments.xml + anchor in "
            "document.xml + relationship + content type). Use to annotate a generated document."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the .docx file"},
                "target_text": {"type": "string", "description": "Text phrase in the document to anchor the comment to"},
                "comment_text": {"type": "string", "description": "Comment body to attach"},
                "author": {"type": "string", "default": "Clixen", "description": "Author name shown on the comment"},
            },
            "required": ["path", "target_text", "comment_text"],
        },
    },
}

CHECK_PDF_ANOMALIES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_pdf_anomalies",
        "description": (
            "Post-render scan of a generated PDF for blank or near-empty pages "
            "(text-extraction length under a threshold). Returns a JSON report listing "
            "page numbers to inspect."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the .pdf file"},
                "min_chars": {"type": "integer", "default": 20, "description": "Minimum non-whitespace chars for a page to be non-empty"},
            },
            "required": ["path"],
        },
    },
}


def validate_docx_executor(args: dict) -> str:
    return validate_docx(path=args["path"])


def repair_docx_element_order_executor(args: dict) -> str:
    return repair_docx_element_order(path=args["path"])


def check_xlsx_quality_executor(args: dict) -> str:
    return check_xlsx_quality(path=args["path"])


def check_pdf_anomalies_executor(args: dict) -> str:
    return check_pdf_anomalies(path=args["path"], min_chars=args.get("min_chars", 20))


def add_docx_comment_executor(args: dict) -> str:
    return add_docx_comment(
        path=args["path"],
        target_text=args["target_text"],
        comment_text=args["comment_text"],
        author=args.get("author", "Clixen"),
    )
