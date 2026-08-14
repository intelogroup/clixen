import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import harness
from tools.structured import read_document


def test_parse_local_fs_action_reads_with_document_router(tmp_path):
    # parse_local_fs_action existence-gates the matched path (_harness_fs_actions.py)
    # so it never proposes a read against a file that isn't actually there.
    report = tmp_path / "report.docx"
    report.write_bytes(b"")

    action = harness.parse_local_fs_action(f"Read {report}")

    assert action.tool_name == "read_document"
    assert action.arguments == {"path": str(report)}


def test_read_document_extracts_docx_text(tmp_path):
    docx = tmp_path / "note.docx"
    document_xml = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Hello local agent</w:t></w:r></w:p>
    <w:p><w:r><w:t>DOCX extraction works</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("word/document.xml", document_xml)

    result = read_document(str(docx))

    assert "DOCX:" in result
    assert "Hello local agent" in result
    assert "DOCX extraction works" in result


def test_read_document_extracts_xlsx_shared_strings(tmp_path):
    xlsx = tmp_path / "sheet.xlsx"
    workbook_xml = """\
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="Data" sheetId="1"/></sheets>
</workbook>
"""
    shared_strings_xml = """\
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <si><t>Name</t></si><si><t>Score</t></si><si><t>Ada</t></si>
</sst>
"""
    sheet_xml = """\
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>
    <row><c t="s"><v>2</v></c><c><v>42</v></c></row>
  </sheetData>
</worksheet>
"""
    with zipfile.ZipFile(xlsx, "w") as zf:
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/sharedStrings.xml", shared_strings_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    result = read_document(str(xlsx))

    assert "XLSX:" in result
    assert "--- Data ---" in result
    assert "Name\tScore" in result
    assert "Ada\t42" in result


def test_read_pdf_honors_page_range_and_emits_page_locators(tmp_path):
    from reportlab.pdfgen import canvas
    from tools.structured import read_pdf

    pdf = tmp_path / "pages.pdf"
    writer = canvas.Canvas(str(pdf))
    for number in range(1, 4):
        writer.drawString(72, 720, f"ONLY PAGE {number}")
        writer.showPage()
    writer.save()

    result = read_pdf(str(pdf), pages="2")

    assert "--- Page 2 ---" in result
    assert "ONLY PAGE 2" in result
    assert "ONLY PAGE 1" not in result
    assert "ONLY PAGE 3" not in result


def test_read_document_inspects_zip_and_extracts_safe_supported_members(tmp_path):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("docs/note.txt", "inside archive")
        zf.writestr("../escape.txt", "must not extract")

    result = read_document(str(archive), max_chars=5000)

    assert "ZIP:" in result
    assert "docs/note.txt" in result
    assert "inside archive" in result
    assert "skipped unsafe path" in result
    assert not (tmp_path.parent / "escape.txt").exists()


def test_read_document_parses_rtf_controls(tmp_path):
    rtf = tmp_path / "note.rtf"
    rtf.write_text(r"{\rtf1\ansi\b Heading\b0\par Body text}")

    result = read_document(str(rtf))

    assert "\\rtf" not in result
    assert "Heading" in result
    assert "Body text" in result


def test_read_document_does_not_treat_legacy_or_binary_suffixes_as_text(tmp_path):
    for suffix in (".doc", ".xls", ".mp3", ".msg"):
        path = tmp_path / f"unknown{suffix}"
        path.write_bytes(b"tiny binary-looking payload")

        result = read_document(str(path))

        assert "Unsupported document format" in result
