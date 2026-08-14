import zipfile
from types import SimpleNamespace


def test_docx_document_agent_preserves_heading_evidence(tmp_path, monkeypatch):
    from agents import document_agent

    docx = tmp_path / "brief.docx"
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        '<w:r><w:t>Decision</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Approve the migration.</w:t></w:r></w:p>'
        '</w:body></w:document>'
    )
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("word/document.xml", xml)

    captured = {}
    monkeypatch.setattr(document_agent, "_chat", lambda **kwargs: (
        captured.update(kwargs) or SimpleNamespace(message=SimpleNamespace(
            content="Approve the migration [E1].", tool_calls=None,
        ))
    ))
    answer = document_agent.run_document_agent(
        f"Summarize {docx}", model="gemma4:12b"
    )

    assert "Approve the migration." in captured["messages"][0]["content"]
    assert "LOCATOR: Decision" in captured["messages"][0]["content"]
    assert "Decision" in answer


def test_xlsx_document_agent_preserves_sheet_evidence(tmp_path, monkeypatch):
    from agents import document_agent
    from openpyxl import Workbook

    xlsx = tmp_path / "budget.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Forecast"
    sheet.append(["Quarter", "Revenue"])
    sheet.append(["Q1", 1200000])
    workbook.save(xlsx)

    captured = {}
    monkeypatch.setattr(document_agent, "_chat", lambda **kwargs: (
        captured.update(kwargs) or SimpleNamespace(message=SimpleNamespace(
            content="Q1 revenue is 1200000 [E1].", tool_calls=None,
        ))
    ))
    answer = document_agent.run_document_agent(
        f"Extract Q1 revenue from {xlsx}", model="gemma4:12b"
    )

    assert "Forecast" in captured["messages"][0]["content"]
    assert "1200000" in captured["messages"][0]["content"]
    assert str(xlsx) in answer


def test_email_document_agent_extracts_headers_and_body(tmp_path, monkeypatch):
    from agents import document_agent

    email = tmp_path / "notice.eml"
    email.write_text(
        "From: sender@example.com\nTo: team@example.com\n"
        "Subject: Renewal notice\n\nRenewal is due on June 30.\n"
    )
    captured = {}
    monkeypatch.setattr(document_agent, "_chat", lambda **kwargs: (
        captured.update(kwargs) or SimpleNamespace(message=SimpleNamespace(
            content="Renewal is due on June 30 [E1].", tool_calls=None,
        ))
    ))

    answer = document_agent.run_document_agent(f"Summarize {email}", model="gemma4:12b")

    prompt = captured["messages"][0]["content"]
    assert "Subject: Renewal notice" in prompt
    assert "Renewal is due on June 30." in prompt
    assert str(email) in answer


def test_html_document_agent_extracts_readable_text(tmp_path, monkeypatch):
    from agents import document_agent

    html = tmp_path / "page.html"
    html.write_text("<html><body><h1>Notice</h1><p>Deadline is Friday.</p></body></html>")
    captured = {}
    monkeypatch.setattr(document_agent, "_chat", lambda **kwargs: (
        captured.update(kwargs) or SimpleNamespace(message=SimpleNamespace(
            content="Deadline is Friday [E1].", tool_calls=None,
        ))
    ))

    document_agent.run_document_agent(f"Read {html}", model="gemma4:12b")

    prompt = captured["messages"][0]["content"]
    assert "Notice" in prompt
    assert "Deadline is Friday." in prompt
    assert "<html>" not in prompt
