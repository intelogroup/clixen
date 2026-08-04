"""Tests for the doc quality gates (tools/doc_quality.py).

Patterns ported from OpenCoworkAI/open-cowork (MIT) and thvroyal/kimi-skills
(pattern reference only): OOXML element-order repair, formula-error scan,
forbidden-function gate, small-aggregate detection, structural docx
validation, and blank/low-content PDF page detection.
"""
import os
import sys
import json
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.doc_quality import (
    add_docx_comment,
    check_pdf_anomalies,
    check_xlsx_quality,
    repair_docx_element_order,
    validate_docx,
)


def _j(raw: str) -> dict:
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Minimal hand-built .docx fixture with deliberately wrong element ordering
# ---------------------------------------------------------------------------

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_ROOTS_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _doc_xml(sect_first: bool = True) -> str:
    # rPr children deliberately out of schema order (b before rFonts), pPr
    # children out of order (jc before spacing), and sectPr NOT last in body.
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
  <w:p>
    <w:pPr>
      <w:jc w:val="center"/>
      <w:spacing w:after="240"/>
      <w:rPr>
        <w:b/>
        <w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>
      </w:rPr>
    </w:pPr>
    <w:r><w:rPr><w:b/><w:rFonts w:ascii="Arial"/></w:rPr><w:t>Hello</w:t></w:r>
  </w:p>
  {"<w:sectPr><w:pgSz w:w=\"12240\" w:h=\"15840\"/></w:sectPr>" if sect_first else ""}
  <w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p>
  {"<w:sectPr><w:pgSz w:w=\"12240\" w:h=\"15840\"/></w:sectPr>" if not sect_first else ""}
</w:body>
</w:document>"""


def _write_docx(path: Path, sect_first: bool = True) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", _CT)
        zf.writestr("_rels/.rels", _ROOTS_RELS)
        zf.writestr("word/document.xml", _doc_xml(sect_first=sect_first))


def _build_docx():
    """Return (correct_path, corrupt_path) temp fixtures."""
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    bad = tmp / "bad.docx"
    _write_docx(bad, sect_first=True)
    return tmp, bad


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def test_validate_docx_flags_sectpr_not_last():
    _, bad = _build_docx()
    result = _j(validate_docx(str(bad)))
    assert result["ok"] is False
    assert any("sectPr" in i for i in result["issues"])


def test_repair_docx_fixes_order_and_sectpr():
    _, bad = _build_docx()
    result = _j(repair_docx_element_order(str(bad)))
    assert result["ok"] is True
    assert result["repaired"] is True
    assert any("sectPr" in c for c in result["changes"])
    # After repair the file must validate clean.
    post = _j(validate_docx(str(bad)))
    assert post["ok"] is True, post


def test_validate_docx_missing_file():
    result = _j(validate_docx("/nonexistent/x.docx"))
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_validate_docx_not_a_zip(tmp_path):
    p = tmp_path / "fake.docx"
    p.write_bytes(b"this is not a zip")
    result = _j(validate_docx(str(p)))
    assert result["ok"] is False
    assert "zip" in result["error"]


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

def test_xlsx_clean_workbook_ok(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = 10
    ws["A2"] = 5
    ws["A3"] = "=SUM(A1:A10)"
    p = tmp_path / "clean.xlsx"
    wb.save(p)
    result = _j(check_xlsx_quality(str(p)))
    assert result["ok"] is True
    assert result["total_formulas"] == 1


def test_xlsx_flags_formula_error(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "#DIV/0!"
    p = tmp_path / "err.xlsx"
    wb.save(p)
    result = _j(check_xlsx_quality(str(p)))
    assert result["ok"] is False
    assert result["formula_errors"].get("#DIV/0!", {}).get("count", 0) == 1


def test_xlsx_flags_forbidden_function(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "=XLOOKUP(B2,B2:B10,C2:C10)"
    p = tmp_path / "forbidden.xlsx"
    wb.save(p)
    result = _j(check_xlsx_quality(str(p)))
    assert result["ok"] is False
    assert any("XLOOKUP" in f for f in result["forbidden_functions"])


def test_xlsx_flags_small_aggregate(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "=SUM(A2:A3)"
    p = tmp_path / "small.xlsx"
    wb.save(p)
    result = _j(check_xlsx_quality(str(p)))
    assert result["ok"] is False
    assert any("SUM" in s for s in result["suspicious_small_aggregates"])


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def test_pdf_anomalies_detects_blank_page(tmp_path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    p = tmp_path / "mixed.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawString(72, 720, "This page has content that is reasonably long.")
    c.showPage()
    c.showPage()  # blank page
    c.save()

    result = _j(check_pdf_anomalies(str(p)))
    assert result["ok"] is False
    assert result["page_count"] == 2
    assert result["blank_pages"] == [2]


def test_pdf_anomalies_clean(tmp_path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    p = tmp_path / "clean.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.drawString(72, 720, "A single fully populated page with enough text on it to pass.")
    c.save()

    result = _j(check_pdf_anomalies(str(p)))
    assert result["ok"] is True
    assert result["blank_pages"] == []


# ---------------------------------------------------------------------------
# DOCX comments
# ---------------------------------------------------------------------------

def _make_docx_with_text(tmp_path, text: str = "Hello strategic world. More content here.") -> Path:
    import docx

    doc = docx.Document()
    doc.add_paragraph(text)
    p = tmp_path / "commented.docx"
    doc.save(p)
    return p


def test_add_docx_comment_wires_valid_parts(tmp_path):
    p = _make_docx_with_text(tmp_path)
    result = _j(add_docx_comment(str(p), "strategic world", "Needs a source citation", "Reviewer"))
    assert result["ok"] is True
    assert result["comment_id"] == 0
    assert result["author"] == "Reviewer"

    with zipfile.ZipFile(p) as zf:
        names = zf.namelist()
        assert "word/comments.xml" in names
        assert "word/_rels/document.xml.rels" in names
        comments = zf.read("word/comments.xml")
        doc_xml = zf.read("word/document.xml")
        ct = zf.read("[Content_Types].xml")

    assert b"Needs a source citation" in comments
    assert b'w:id="0"' in comments
    assert b"commentRangeStart" in doc_xml
    assert b"commentRangeEnd" in doc_xml
    assert b"commentReference" in doc_xml
    assert b"comments.xml" in ct

    # The commented file must still pass structural validation.
    post = _j(validate_docx(str(p)))
    assert post["ok"] is True, post


def test_add_docx_comment_increments_id(tmp_path):
    p = _make_docx_with_text(tmp_path)
    r1 = _j(add_docx_comment(str(p), "strategic world", "first"))
    r2 = _j(add_docx_comment(str(p), "more content", "second"))
    assert r1["comment_id"] == 0
    assert r2["comment_id"] == 1
    with zipfile.ZipFile(p) as zf:
        comments = zf.read("word/comments.xml")
    assert comments.count(b"<w:comment ") >= 2


def test_add_docx_comment_missing_target(tmp_path):
    p = _make_docx_with_text(tmp_path)
    result = _j(add_docx_comment(str(p), "no such phrase anywhere", "x"))
    assert result["ok"] is False
    assert "not found" in result["error"]
