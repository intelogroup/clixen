"""
Regression test: read_specialist must resolve file paths containing SPACES.

Confirmed live (chat_ui.log 2026-08-03): "extract text from the PDF file at
/Users/kalinovdameus/developer/benoucheca/perso/006-006 Haiti oxygen strategy
outline.pdf" — the old extractor regex stopped at the first space, resolved to
nothing, and the read fell into the local-Ollama ReAct loop (which then refused
the PDF with "encoding issues"). The tools themselves were always fine once the
full path was handed over.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.specialists.read_specialist import (
    _fast_read,
    _resolve_file_from_query,
)


def _make_fixture(tmp_path: Path):
    d = tmp_path / "perso"
    d.mkdir()
    pdf = d / "006-006 Haiti oxygen strategy outline.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake pdf bytes")
    md = d / "006-006 Haiti oxygen strategy outline.md"
    md.write_text("# DRAFT REPORT\nStrategic Plan for Sustainable Medical Oxygen in Haiti\n")
    txt = d / "notes 2026.txt"
    txt.write_text("hello spacey world\n")
    return d, pdf, md, txt


def test_resolve_absolute_path_with_spaces(tmp_path):
    d, pdf, md, txt = _make_fixture(tmp_path)
    q = f"extract text from the PDF file at {pdf}"
    assert _resolve_file_from_query(q) == str(pdf)


def test_resolve_md_path_with_spaces(tmp_path):
    d, pdf, md, txt = _make_fixture(tmp_path)
    q = f"verify the {md}"
    assert _resolve_file_from_query(q) == str(md)


def test_resolve_trailing_prose_after_path(tmp_path):
    d, pdf, md, txt = _make_fixture(tmp_path)
    q = f"read {txt} and summarize it"
    assert _resolve_file_from_query(q) == str(txt)


def test_fast_read_uses_deterministic_tool_for_spaced_pdf(tmp_path):
    d, pdf, md, txt = _make_fixture(tmp_path)
    r = _fast_read(str(pdf))
    assert r.tool_used == "read_pdf"
    assert r.error is None


def test_no_resolution_for_pathless_query(tmp_path):
    assert _resolve_file_from_query("hello how are you") is None
    assert _resolve_file_from_query("read this file") is None


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for fn in (
            test_resolve_absolute_path_with_spaces,
            test_resolve_md_path_with_spaces,
            test_resolve_trailing_prose_after_path,
            test_fast_read_uses_deterministic_tool_for_spaced_pdf,
            test_no_resolution_for_pathless_query,
        ):
            fn(Path(td))
    print("read_specialist space-safe resolution tests passed")
