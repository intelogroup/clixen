import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import harness
from tools.structured import read_document


def test_parse_local_fs_action_reads_with_document_router():
    action = harness.parse_local_fs_action("Read /tmp/report.docx")

    assert action.tool_name == "read_document"
    assert action.arguments == {"path": "/tmp/report.docx"}


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
