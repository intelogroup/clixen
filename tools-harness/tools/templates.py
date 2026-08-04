"""Template gallery: pre-built docx/xlsx/pptx files with {{placeholder}} text,
substituted via plain str.replace over runs/cells/text frames — no templating
engine, one bounded precise substitution (see CLAUDE.md design principles)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

TEMPLATES_DIR = Path(__file__).parent.parent / "doc_templates"
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _manifest() -> dict[str, Any]:
    return json.loads((TEMPLATES_DIR / "manifest.json").read_text())


def list_templates() -> list[dict]:
    return [{"name": name, **meta} for name, meta in _manifest().items()]


def _sub(text: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), text)


def render_template(name: str, values: dict[str, str], output_path: str) -> str:
    meta = _manifest().get(name)
    if not meta:
        raise ValueError(f"unknown template {name!r}. Available: {list(_manifest())}")
    src = TEMPLATES_DIR / f"{name}.{meta['format']}"
    out = Path(output_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    if meta["format"] == "docx":
        from docx import Document
        doc = Document(str(src))
        for p in doc.paragraphs:
            for run in p.runs:
                run.text = _sub(run.text, values)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        for run in p.runs:
                            run.text = _sub(run.text, values)
        doc.save(str(out))
    elif meta["format"] == "xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(str(src))
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.value = _sub(cell.value, values)
        wb.save(str(out))
    elif meta["format"] == "pptx":
        import pptx
        prs = pptx.Presentation(str(src))
        for slide in prs.slides:
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                for p in shape.text_frame.paragraphs:
                    for run in p.runs:
                        run.text = _sub(run.text, values)
        prs.save(str(out))
    else:
        raise ValueError(f"unsupported template format {meta['format']!r}")

    return str(out)


if __name__ == "__main__":
    import tempfile

    _VALUES = {
        "status_report": {"title": "Weekly Status", "period": "W31", "author": "Jim",
                           "summary": "All green.", "next_steps": "Ship it."},
        "invoice": {"invoice_number": "1001", "client_name": "Acme", "date": "2026-08-04",
                    "line_item": "Consulting", "amount": "500", "total": "500"},
        "pitch_deck": {"company_name": "Clixen", "tagline": "Local-first agents",
                        "problem": "Cloud lock-in.", "solution": "On-device LLM harness."},
    }

    with tempfile.TemporaryDirectory() as td:
        for name, values in _VALUES.items():
            fmt = _manifest()[name]["format"]
            out = render_template(name, values, f"{td}/out_{name}.{fmt}")
            assert Path(out).exists() and Path(out).stat().st_size > 0

            if fmt == "docx":
                from docx import Document
                doc = Document(out)
                text = "\n".join(p.text for p in doc.paragraphs)
                assert "{{" not in text, f"{name}: unfilled placeholder remains"
            elif fmt == "xlsx":
                import openpyxl
                wb = openpyxl.load_workbook(out)
                for ws in wb.worksheets:
                    for row in ws.iter_rows():
                        for cell in row:
                            if isinstance(cell.value, str):
                                assert "{{" not in cell.value, f"{name}: unfilled placeholder remains"
            elif fmt == "pptx":
                import pptx
                prs = pptx.Presentation(out)
                for slide in prs.slides:
                    for shape in slide.shapes:
                        if shape.has_text_frame:
                            assert "{{" not in shape.text_frame.text, f"{name}: unfilled placeholder remains"

    print("templates.py self-check: OK")
