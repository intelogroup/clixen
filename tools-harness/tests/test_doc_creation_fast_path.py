"""The deterministic document-creation path (telegram_bot._run_doc_agent).

Replaced the old gemma4 tool-calling agent loop (80s/round, hung on long
prompts) with: gather source -> one synthesis call -> deterministic convert.
These tests stub the single LLM call so no Ollama is needed, and verify:
  - format detection picks the right extension
  - pdf/docx actually land on disk
  - a converter that returns an ERROR STRING instead of raising (pptx with
    python-pptx missing) degrades to a clean message, not a bogus path that
    would crash the Telegram send
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import clients.ollama_client as oc
import store.conversation as sc


@pytest.fixture
def doc_env(monkeypatch):
    # Stub the single synthesis completion — no Ollama. Format-aware: the xlsx
    # branch asks for CSV, everything else asks for Markdown.
    def _fake_chat(user_message, model=None, tools=None, **k):
        if "CSV" in user_message:
            return "Team,Score\nArgentina,2\nFrance,1\n"
        return "# Title\n\n## Section\n- a\n- b\n"
    monkeypatch.setattr(oc, "chat", _fake_chat)
    monkeypatch.setattr(sc, "get", lambda cid: [{"role": "assistant", "content": "source"}])
    import telegram_bot as tb
    return tb


def test_doc_format_detection(doc_env):
    tb = doc_env
    assert tb._doc_format("make a pdf of this") == ("PDF", ".pdf")
    assert tb._doc_format("generate a word document") == ("Word document", ".docx")
    assert tb._doc_format("turn this into slides") == ("presentation", ".pptx")
    assert tb._doc_format("send it as an excel sheet") == ("Excel spreadsheet", ".xlsx")
    assert tb._doc_format("give me a spreadsheet") == ("Excel spreadsheet", ".xlsx")


def test_strip_fences(doc_env):
    tb = doc_env
    assert tb._strip_fences("```csv\nA,B\n1,2\n```") == "A,B\n1,2"
    assert tb._strip_fences("```\n# Title\n```") == "# Title"
    assert tb._strip_fences("# no fence") == "# no fence"


def test_content_query_strips_format_words(doc_env):
    # Format words must not reach web search, or it returns spreadsheet templates
    # instead of the actual content (the xlsx "NO DATA" bug).
    tb = doc_env
    assert "excel" not in tb._content_query("today's matches as an excel sheet").lower()
    assert "pdf" not in tb._content_query("world cup scores in a pdf").lower()
    assert "spreadsheet" not in tb._content_query("give me a spreadsheet of results").lower()
    # a query with no format word is unchanged
    assert tb._content_query("today's world cup matches") == "today's world cup matches"


def _cleanup(path, ext):
    if path and os.path.exists(path):
        os.remove(path)
    for inter_ext in (".md", ".csv"):  # intermediate written before conversion
        inter = (path or "")[: -len(ext)] + inter_ext
        if os.path.exists(inter):
            os.remove(inter)


def test_pdf_docx_xlsx_land_on_disk(doc_env):
    tb = doc_env
    for req, ext in [("make a pdf", ".pdf"), ("make a word document", ".docx"),
                     ("make an excel sheet", ".xlsx")]:
        text, path = tb._run_doc_agent(req, "gemma4:12b-mlx", "t", on_token=None)
        assert path and path.endswith(ext), (req, path)
        assert os.path.exists(path) and os.path.getsize(path) > 0
        _cleanup(path, ext)


def test_xlsx_has_real_rows(doc_env):
    # The CSV synthesis (Team,Score / Argentina,2 / France,1) must become a real
    # 3-row sheet (header + 2 data rows), not a single-cell dump.
    import openpyxl
    tb = doc_env
    _text, path = tb._run_doc_agent("send today's scores as an excel sheet", "gemma4:12b-mlx", "t", on_token=None)
    assert path and path.endswith(".xlsx")
    ws = openpyxl.load_workbook(path).active
    rows = [tuple(str(c) for c in r) for r in ws.iter_rows(values_only=True)]
    assert rows[0] == ("Team", "Score")
    assert ("Argentina", "2") in rows and ("France", "1") in rows
    _cleanup(path, ".xlsx")


def test_error_string_converter_degrades_cleanly(doc_env, monkeypatch):
    # Force the "python-pptx missing" scenario explicitly rather than relying on it being
    # absent from the environment (python-pptx is installed in dev/CI here, so the real
    # converter would otherwise just succeed and this test would pass for the wrong reason).
    # markdown_to_pptx is imported locally inside _run_doc_agent, so patch it at its source.
    monkeypatch.setattr(
        "tools.document_create.markdown_to_pptx",
        lambda md_path, output_path, template="": "Error: python-pptx not installed",
    )
    tb = doc_env
    text, path = tb._run_doc_agent("make a powerpoint", "gemma4:12b-mlx", "t", on_token=None)
    assert path is None
    assert "couldn" in text.lower()


def test_confirmation_goes_to_on_token_not_the_body(doc_env):
    # TTS should speak "Here's your PDF.", never the whole document body.
    tb = doc_env
    tokens = []
    text, path = tb._run_doc_agent("make a pdf", "gemma4:12b-mlx", "t", on_token=tokens.append)
    assert tokens == ["Here's your PDF."]
    assert text == "Here's your PDF."
    _cleanup(path, ".pdf")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
