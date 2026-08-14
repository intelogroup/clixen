"""
Office document processing utilities.

Extracts readable text from Excel (.xlsx/.xls), Word (.docx/.doc),
PowerPoint (.pptx/.ppt), and CSV files, and writes a plain-text markdown
summary alongside the source file.
Mirrors the (path, content) return contract of pdf_tools.pdf_to_markdown.
"""

from __future__ import annotations

from pathlib import Path


class PasswordProtectedError(Exception):
    """Raised when a file is encrypted/password-protected and cannot be read."""


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

def excel_to_markdown(file_path: str, output_dir: str | None = None) -> tuple[str, str]:
    """
    Convert an Excel workbook to a markdown document.

    Each sheet becomes a level-2 heading.  Up to 200 rows × 20 columns
    per sheet are included to keep the summary prompt manageable.

    Returns:
        (md_file_path_str, md_content_str)
    """
    import anydoc

    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Excel file not found: {file_path}")

    out_dir = Path(output_dir) if output_dir is not None else p.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        md_content = anydoc.to_markdown(str(p))
    except Exception as exc:
        msg = str(exc).lower()
        if "encrypted" in msg or "password" in msg:
            raise PasswordProtectedError(f"Excel file is password-protected: {p.name}") from exc
        raise

    fill_grid = _excel_fill_colors(p)
    if fill_grid:
        md_content = f"{md_content}\n\n{fill_grid}" if md_content.strip() else fill_grid

    md_file = out_dir / f"{p.stem}.md"
    md_file.write_text(md_content, encoding="utf-8")
    return (str(md_file), md_content)


def _excel_fill_colors(p: Path) -> str:
    """Grid of per-cell fill colors, keyed by A1 address — anydoc's markdown
    conversion only carries text/values, so any GAIA-style question about
    cell color (e.g. "which plots are green") sees nothing without this."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(str(p), data_only=True)
    except Exception:
        return ""

    sections = []
    for sheet in wb.worksheets:
        rows = []
        for row in sheet.iter_rows(max_row=200, max_col=20):
            for cell in row:
                fill = cell.fill
                if fill is None or fill.patternType is None:
                    continue
                color = fill.fgColor.rgb if fill.fgColor else None
                if not color or not isinstance(color, str) or color in ("00000000", "FFFFFFFF"):
                    continue
                rows.append(f"{cell.coordinate}: #{color[-6:]}")
        if rows:
            sections.append(f"### {sheet.title} — cell fill colors\n" + "\n".join(rows))
    return "\n\n".join(sections)


def excel_metadata(file_path: str) -> dict:
    """Return sheet names and row counts without reading cell values."""
    import openpyxl

    p = Path(file_path)
    wb = openpyxl.load_workbook(str(p), read_only=True)
    meta = {}
    for name in wb.sheetnames:
        ws = wb[name]
        meta[name] = ws.max_row or 0
    wb.close()
    return meta


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

def docx_to_markdown(file_path: str, output_dir: str | None = None) -> tuple[str, str]:
    """
    Convert a Word document (.docx) to a markdown document.

    Headings map to markdown # levels; normal paragraphs are plain text.
    Tables are rendered as pipe-delimited rows.

    Returns:
        (md_file_path_str, md_content_str)
    """
    import anydoc

    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Word file not found: {file_path}")

    out_dir = Path(output_dir) if output_dir is not None else p.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        md_content = anydoc.to_markdown(str(p))
    except Exception as exc:
        msg = str(exc).lower()
        if "encrypted" in msg or "password" in msg:
            raise PasswordProtectedError(f"Word file is password-protected: {p.name}") from exc
        raise

    comments_md = _docx_comments_markdown(str(p))
    if comments_md:
        md_content = f"{md_content}\n\n{comments_md}"

    md_file = out_dir / f"{p.stem}.md"
    md_file.write_text(md_content, encoding="utf-8")
    return (str(md_file), md_content)


def _docx_comments_markdown(file_path: str) -> str:
    """Extract Word review comments (python-docx/anydoc don't expose these)."""
    from docx2python import docx2python

    try:
        result = docx2python(file_path, html=False)
        comments = result.comments or []
    except Exception:
        return ""

    if not comments:
        return ""

    lines = ["## Comments"]
    for anchor, author, date, text in comments:
        anchor = anchor.strip()
        quote = f' on "{anchor}"' if anchor and anchor != "." else ""
        lines.append(f"- **{author}** ({date}){quote}: {text}")
    return "\n".join(lines)


def _iter_blocks(doc) -> list[str]:
    """Yield markdown-formatted strings for paragraphs and tables in order."""
    import docx as _docx
    from docx.oxml.ns import qn

    blocks: list[str] = []
    body = doc.element.body

    for child in body:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "p":
            para = _docx.text.paragraph.Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            style = para.style.name if para.style else ""
            if style.startswith("Heading 1"):
                blocks.append(f"\n# {text}")
            elif style.startswith("Heading 2"):
                blocks.append(f"\n## {text}")
            elif style.startswith("Heading 3"):
                blocks.append(f"\n### {text}")
            else:
                blocks.append(text)
        elif tag == "tbl":
            table = _docx.table.Table(child, doc)
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                blocks.append("| " + " | ".join(cells) + " |")

    return blocks


def docx_page_count(file_path: str) -> int:
    """
    Approximate page count from the Word document's built-in page count property.
    Returns 0 if not available.
    """
    import docx as _docx

    try:
        doc = _docx.Document(file_path)
        props = doc.core_properties
        # python-docx doesn't expose page count directly; fall back to 0
        _ = props.title  # just to confirm the file opens
        return 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# PowerPoint / CSV (anydoc-only formats, no prior converter existed)
# ---------------------------------------------------------------------------

def _anydoc_to_markdown(file_path: str, output_dir: str | None, kind: str) -> tuple[str, str]:
    import anydoc

    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"{kind} file not found: {file_path}")

    out_dir = Path(output_dir) if output_dir is not None else p.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        md_content = anydoc.to_markdown(str(p))
    except Exception as exc:
        msg = str(exc).lower()
        if "encrypted" in msg or "password" in msg:
            raise PasswordProtectedError(f"{kind} file is password-protected: {p.name}") from exc
        raise

    md_file = out_dir / f"{p.stem}.md"
    md_file.write_text(md_content, encoding="utf-8")
    return (str(md_file), md_content)


def pptx_to_markdown(file_path: str, output_dir: str | None = None) -> tuple[str, str]:
    """Convert a PowerPoint deck (.pptx/.ppt/.pps/...) to a markdown document.

    Returns:
        (md_file_path_str, md_content_str)
    """
    return _anydoc_to_markdown(file_path, output_dir, "PowerPoint")


def csv_to_markdown(file_path: str, output_dir: str | None = None) -> tuple[str, str]:
    """Convert a CSV file to a markdown table.

    Returns:
        (md_file_path_str, md_content_str)
    """
    return _anydoc_to_markdown(file_path, output_dir, "CSV")
