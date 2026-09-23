import json
import os
import stat
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import structured


def _fake_liteparse(tmp_path, pages):
    """Stub CLI that writes `pages` as liteparse-shaped JSON to the -o path."""
    script = tmp_path / "liteparse"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "out = sys.argv[sys.argv.index('-o') + 1]\n"
        f"json.dump({json.dumps({'pages': pages})}, open(out, 'w'))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_liteparse_pages_render_with_page_headers(tmp_path, monkeypatch):
    stub = _fake_liteparse(tmp_path, [{"page": 3, "text": "scanned page three text"}])
    monkeypatch.setattr(structured.shutil, "which", lambda n: str(stub) if n == "liteparse" else None)

    out = structured._liteparse_pdf_pages("/x/doc.pdf", 3, 3, 9)

    assert "--- Page 3 ---" in out
    assert "scanned page three text" in out
    assert "9 pages total" in out


def test_liteparse_missing_binary_returns_empty(monkeypatch):
    monkeypatch.setattr(structured.shutil, "which", lambda n: None)
    assert structured._liteparse_pdf_pages("/x/doc.pdf", 1, 1, 1) == ""


def test_liteparse_blank_text_returns_empty_so_caller_falls_back(tmp_path, monkeypatch):
    stub = _fake_liteparse(tmp_path, [{"page": 1, "text": "  "}])
    monkeypatch.setattr(structured.shutil, "which", lambda n: str(stub) if n == "liteparse" else None)
    assert structured._liteparse_pdf_pages("/x/doc.pdf", 1, 1, 1) == ""


def test_read_pdf_prefers_liteparse_then_falls_back(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(structured, "_pdf_page_count", lambda p: 1)
    monkeypatch.setattr(structured, "_pdftotext_pages", lambda p, s, e: "")
    monkeypatch.setattr(structured, "_ocr_pdf_pages", lambda *a: "OLD OCR")

    monkeypatch.setattr(structured, "_liteparse_pdf_pages", lambda *a: "LITEPARSE")
    assert structured.read_pdf(str(pdf)) == "LITEPARSE"

    monkeypatch.setattr(structured, "_liteparse_pdf_pages", lambda *a: "")
    assert structured.read_pdf(str(pdf)) == "OLD OCR"
