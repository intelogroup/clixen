"""
Chart embedding for generated documents — DOCX gets a matplotlib-rendered PNG
(python-docx has no native chart part without hand-built OOXML plumbing),
XLSX and PPTX get real native chart objects (openpyxl / python-pptx support
them directly).

Chart spec shape (shared across all three):
    {"type": "bar"|"line"|"pie", "title": str, "categories": [str, ...],
     "series": {"Series name": [number, ...], ...}}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_chart_png(spec: dict[str, Any], out_path: str) -> str:
    """Render a chart spec to a PNG via matplotlib (Agg backend, headless-safe)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    chart_type = spec.get("type", "bar")
    title = spec.get("title", "")
    categories = spec.get("categories", [])
    series = spec.get("series", {})

    fig, ax = plt.subplots(figsize=(7, 4.5))
    if chart_type == "pie":
        # pie charts plot a single series against categories
        values = next(iter(series.values()), [])
        ax.pie(values, labels=categories, autopct="%1.1f%%")
    elif chart_type == "line":
        for name, values in series.items():
            ax.plot(categories, values, marker="o", label=name)
        ax.legend()
    else:  # bar (default)
        import numpy as np
        x = np.arange(len(categories))
        n = max(len(series), 1)
        width = 0.8 / n
        for i, (name, values) in enumerate(series.items()):
            ax.bar(x + i * width, values, width, label=name)
        ax.set_xticks(x + width * (n - 1) / 2)
        ax.set_xticklabels(categories)
        if n > 1:
            ax.legend()

    if title:
        ax.set_title(title)
    fig.tight_layout()

    out_p = Path(out_path).expanduser()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_p), dpi=150)
    plt.close(fig)
    return str(out_p)


def add_chart_to_docx_png(doc, spec: dict[str, Any], tmp_dir: str) -> None:
    """Render spec to a temp PNG and insert it into a python-docx Document."""
    from docx.shared import Inches

    tmp_path = Path(tmp_dir).expanduser() / "_chart_tmp.png"
    render_chart_png(spec, str(tmp_path))
    doc.add_picture(str(tmp_path), width=Inches(6))
    tmp_path.unlink(missing_ok=True)


def add_native_xlsx_chart(ws, spec: dict[str, Any], anchor: str = "E2") -> None:
    """Add a native openpyxl chart object anchored on the worksheet.

    Assumes the sheet already has a header row + data rows written starting
    at A1 (as data_to_xlsx does) — categories are read from column A, each
    series from the columns matching spec["series"] keys in the header row.
    """
    from openpyxl.chart import BarChart, LineChart, PieChart, Reference

    chart_type = spec.get("type", "bar")
    title = spec.get("title", "")
    series_names = list(spec.get("series", {}).keys())
    n_rows = ws.max_row
    n_cols = ws.max_column

    chart_cls = {"bar": BarChart, "line": LineChart, "pie": PieChart}.get(chart_type, BarChart)
    chart = chart_cls()
    chart.title = title

    # Assume series columns are contiguous and start at column 2 (col A = categories/labels)
    data = Reference(ws, min_col=2, max_col=n_cols, min_row=1, max_row=n_rows)
    cats = Reference(ws, min_col=1, min_row=2, max_row=n_rows)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    ws.add_chart(chart, anchor)


def add_native_pptx_chart(slide, spec: dict[str, Any]) -> None:
    """Add a native python-pptx chart to a slide."""
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    chart_type = spec.get("type", "bar")
    xl_type = {
        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "line": XL_CHART_TYPE.LINE,
        "pie": XL_CHART_TYPE.PIE,
    }.get(chart_type, XL_CHART_TYPE.COLUMN_CLUSTERED)

    chart_data = CategoryChartData()
    chart_data.categories = spec.get("categories", [])
    for name, values in spec.get("series", {}).items():
        chart_data.add_series(name, values)

    slide.shapes.add_chart(xl_type, Inches(1), Inches(1.5), Inches(8), Inches(5), chart_data)


if __name__ == "__main__":
    import tempfile

    _spec = {
        "type": "bar",
        "title": "Q1 Revenue by Region",
        "categories": ["East", "West"],
        "series": {"Revenue": [120, 95]},
    }

    with tempfile.TemporaryDirectory() as td:
        # PNG render (bar/line/pie all produce non-zero-byte files)
        for t in ("bar", "line", "pie"):
            spec = dict(_spec, type=t)
            out = Path(td) / f"{t}.png"
            render_chart_png(spec, str(out))
            assert out.exists() and out.stat().st_size > 0, f"{t} chart PNG empty/missing"

        # DOCX embed
        from docx import Document
        doc = Document()
        add_chart_to_docx_png(doc, _spec, td)
        assert len(doc.inline_shapes) == 1, "expected one embedded picture in docx"

        # XLSX native chart
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Region", "Revenue"])
        ws.append(["East", 120])
        ws.append(["West", 95])
        add_native_xlsx_chart(ws, _spec)
        assert len(ws._charts) == 1, "expected one native xlsx chart"

        # PPTX native chart
        import pptx
        prs = pptx.Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        add_native_pptx_chart(slide, _spec)
        chart_shapes = [s for s in slide.shapes if s.has_chart]
        assert len(chart_shapes) == 1, "expected one native pptx chart"

    print("chart_embed self-check: OK")
