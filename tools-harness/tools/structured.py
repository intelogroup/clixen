"""
Structured file reading — parses JSON, YAML, TOML, CSV, PDF, Office documents,
images, and source files into model-friendly summaries with extracted structure.

Tools:
  read_document — route common document types to the right extractor
  parse_file   — auto-detect format and return parsed/pretty-printed content
  read_pdf     — extract text from PDF files page by page
  parse_code   — extract functions, classes, imports from source code
"""

import ast
import csv
import html
import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

READ_DOCUMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_document",
        "description": (
            "Read a local document using the best available extractor for its file type. "
            "Use this before read_file when the file may be PDF, DOCX, XLSX, image, "
            "structured data, source code, or another document format."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the document",
                },
                "pages": {
                    "type": "string",
                    "description": "For PDFs: page range like '1-5' or '3' (default: first 10 pages)",
                    "default": "1-10",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return (default 12000)",
                    "default": 12000,
                },
                "lang": {
                    "type": "string",
                    "description": "For image OCR: language code such as 'en' or 'fr'",
                    "default": "en",
                },
                "redact": {
                    "type": "boolean",
                    "description": (
                        "Scan extracted text for PII/PHI (names, dates, SSN, email, phone) "
                        "using a local NER model and append a summary of what was found. "
                        "Text itself is left unchanged (flag, not strip)."
                    ),
                    "default": False,
                },
            },
            "required": ["path"],
        },
    },
}

PARSE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "parse_file",
        "description": (
            "Read a structured file (JSON, YAML, TOML, CSV, .env) and return it "
            "in a clean, readable format. Better than read_file for config files "
            "and data files — handles encoding, nesting, and large CSVs gracefully."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file",
                },
                "max_rows": {
                    "type": "integer",
                    "description": "For CSV: max rows to show (default 50)",
                    "default": 50,
                },
            },
            "required": ["path"],
        },
    },
}

READ_PDF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_pdf",
        "description": (
            "Extract text from a PDF file, page by page. "
            "Use for research papers, reports, or any PDF document on this computer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the PDF file",
                },
                "pages": {
                    "type": "string",
                    "description": (
                        "Page range like '1-30' or '3' (default: first 10 pages). "
                        "Output is capped at 30000 chars total — for long documents, "
                        "request pages in batches of ~25-30 rather than a few at a time."
                    ),
                    "default": "1-10",
                },
            },
            "required": ["path"],
        },
    },
}

PARSE_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "parse_code",
        "description": (
            "Parse a source code file and extract its structure: imports, classes, "
            "functions with signatures and docstrings. Returns a map of the file "
            "without needing to read all the implementation details. "
            "Supports Python natively; other languages via regex."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the source file",
                },
                "include_bodies": {
                    "type": "boolean",
                    "description": "Include function bodies (default false — just signatures)",
                    "default": False,
                },
            },
            "required": ["path"],
        },
    },
}

# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".ini", ".cfg", ".conf",
    ".html", ".htm", ".css", ".scss", ".sql", ".sh", ".bash", ".zsh",
}
_CODE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cc",
    ".cpp", ".h", ".hpp", ".cs", ".php", ".rb", ".swift", ".kt", ".m",
}
_STRUCTURED_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".csv", ".env"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def read_document(
    path: str,
    pages: str = "1-10",
    max_chars: int = 12000,
    lang: str = "en",
    redact: bool = False,
) -> str:
    """Route a local document path to the best available text extractor.

    redact=True pipes the extracted text through tools.pii_redact.redact_pii
    (flag mode: text unchanged, a summary of detected entities appended) —
    kept out of each format-specific reader so PDF/docx/etc extraction stays
    untouched.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"File not found: {p}"
    if not p.is_file():
        return f"Not a file: {p}"

    suffix = p.suffix.lower()
    max_chars = max(1000, min(int(max_chars or 12000), 50000))

    if suffix == ".pdf":
        text = read_pdf(str(p), pages=pages)[:max_chars]
    elif suffix == ".docx":
        text = _read_docx(p, max_chars=max_chars)
    elif suffix == ".xlsx":
        text = _read_xlsx(p, max_chars=max_chars)
    elif suffix == ".pages":
        text = _read_pages(p, max_chars=max_chars)
    elif suffix in _STRUCTURED_SUFFIXES:
        text = parse_file(str(p))[:max_chars]
    elif suffix in _CODE_SUFFIXES:
        text = parse_code(str(p), include_bodies=False)[:max_chars]
    elif suffix in _IMAGE_SUFFIXES:
        try:
            from tools.ocr import execute as ocr_execute
        except Exception as e:
            return f"OCR unavailable for {p}: {e}"
        text = ocr_execute(str(p), lang=lang)[:max_chars]
    elif suffix in _TEXT_SUFFIXES or _looks_like_text(p):
        try:
            from tools.filesystem import read_file
        except Exception:
            text = p.read_text(errors="replace")[:max_chars]
        else:
            text = read_file(str(p), limit=400)[:max_chars]
    else:
        return (
            f"Unsupported document format: {p.suffix or '(no extension)'}\n"
            f"Path: {p}\n"
            "Supported: text/code files, JSON/YAML/TOML/CSV/.env, PDF, DOCX, XLSX, "
            "Pages (read-only), and image OCR (PNG/JPG/TIFF/BMP/WebP). "
            "For Numbers, Keynote, legacy .doc/.xls, audio, or video, convert/export "
            "to a supported format first."
        )

    if redact:
        from tools.pii_redact import redact_pii

        result = redact_pii(text, mode="flag")
        if result["entities"]:
            summary = ", ".join(f"{e['label']}:{e['text']!r}" for e in result["entities"])
            text += f"\n\n[PII/PHI detected — {len(result['entities'])} entities: {summary}]"
    return text


def _looks_like_text(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except (PermissionError, OSError):
        return False
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _xml_text(elem: ET.Element) -> str:
    return html.unescape("".join(elem.itertext())).strip()


def _read_docx(path: Path, max_chars: int) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = [
                "word/document.xml",
                *sorted(n for n in zf.namelist() if n.startswith("word/header") and n.endswith(".xml")),
                *sorted(n for n in zf.namelist() if n.startswith("word/footer") and n.endswith(".xml")),
            ]
            parts = [f"DOCX: {path}"]
            for name in names:
                if name not in zf.namelist():
                    continue
                root = ET.fromstring(zf.read(name))
                lines = []
                for elem in root.iter():
                    if elem.tag.endswith("}p") or elem.tag.endswith("}tr"):
                        text = _xml_text(elem)
                        if text:
                            lines.append(text)
                if lines:
                    parts.append("\n".join(lines))
            result = "\n\n".join(parts).strip()
            return result[:max_chars] if result else f"No text found in DOCX: {path}"
    except zipfile.BadZipFile:
        return f"DOCX read error: not a valid .docx zip file: {path}"
    except Exception as e:
        return f"DOCX read error: {e}"


def _read_xlsx(path: Path, max_chars: int) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            shared = _xlsx_shared_strings(zf)
            sheet_names = _xlsx_sheet_names(zf)
            sheet_files = sorted(n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
            parts = [f"XLSX: {path}"]
            for idx, sheet_file in enumerate(sheet_files, 1):
                label = sheet_names.get(idx, f"Sheet {idx}")
                rows = _xlsx_rows(zf, sheet_file, shared, max_rows=50)
                if rows:
                    parts.append(f"\n--- {label} ---\n" + "\n".join(rows))
                if sum(len(p) for p in parts) >= max_chars:
                    break
            return "\n".join(parts)[:max_chars]
    except zipfile.BadZipFile:
        return f"XLSX read error: not a valid .xlsx zip file: {path}"
    except Exception as e:
        return f"XLSX read error: {e}"


def _extract_printable_runs(data: bytes, min_len: int = 6) -> list[str]:
    """Scan raw bytes for printable-ASCII/UTF-8 runs of length >= min_len.

    Document.iwa is Snappy-compressed protobuf, but Snappy stores literal
    text runs uncompressed inline — a raw byte scan recovers real body text
    without decoding protobuf or Snappy.
    """
    runs: list[str] = []
    start = None
    for i, b in enumerate(data):
        printable = 0x20 <= b <= 0x7E or b >= 0xC2
        if printable and start is None:
            start = i
        elif not printable and start is not None:
            if i - start >= min_len:
                try:
                    runs.append(data[start:i].decode("utf-8"))
                except UnicodeDecodeError:
                    pass
            start = None
    if start is not None and len(data) - start >= min_len:
        try:
            runs.append(data[start:].decode("utf-8"))
        except UnicodeDecodeError:
            pass
    return runs


def _read_pages(path: Path, max_chars: int) -> str:
    """Read-only text extraction for Apple Pages files (.pages).

    No public create/edit/style API exists locally — Pages.app owns that.
    This recovers body text only, via raw byte-scan on Index/Document.iwa
    (see _extract_printable_runs).
    """
    try:
        with zipfile.ZipFile(path) as zf:
            if "Index/Document.iwa" not in zf.namelist():
                return f"Pages read error: no Index/Document.iwa found in {path}"
            data = zf.read("Index/Document.iwa")
        runs = _extract_printable_runs(data)
        survivors = []
        for run in runs:
            letters = sum(c.isalpha() for c in run)
            if letters * 2 >= len(run) and any(c.isspace() for c in run):
                survivors.append(run)
        if not survivors:
            return f"No readable text found in Pages file: {path}"
        result = f"PAGES: {path}\n" + "\n".join(survivors)
        return result[:max_chars]
    except zipfile.BadZipFile:
        return f"Pages read error: not a valid .pages zip file: {path}"
    except Exception as e:
        return f"Pages read error: {e}"


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return [_xml_text(si) for si in root]


def _xlsx_sheet_names(zf: zipfile.ZipFile) -> dict[int, str]:
    if "xl/workbook.xml" not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read("xl/workbook.xml"))
    names = {}
    for idx, sheet in enumerate((e for e in root.iter() if e.tag.endswith("}sheet")), 1):
        names[idx] = sheet.attrib.get("name", f"Sheet {idx}")
    return names


def _xlsx_rows(
    zf: zipfile.ZipFile,
    sheet_file: str,
    shared: list[str],
    max_rows: int,
) -> list[str]:
    root = ET.fromstring(zf.read(sheet_file))
    rows = []
    for row in (e for e in root.iter() if e.tag.endswith("}row")):
        values = []
        for cell in (e for e in row if e.tag.endswith("}c")):
            cell_type = cell.attrib.get("t", "")
            value = ""
            if cell_type == "inlineStr":
                value = _xml_text(cell)
            else:
                v = next((e for e in cell if e.tag.endswith("}v")), None)
                if v is not None and v.text is not None:
                    value = v.text
                    if cell_type == "s":
                        try:
                            value = shared[int(value)]
                        except (ValueError, IndexError):
                            pass
            values.append(value)
        if any(v != "" for v in values):
            rows.append("\t".join(values))
        if len(rows) >= max_rows:
            break
    return rows


def parse_file(path: str, max_rows: int = 50) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"File not found: {p}"
    if not p.is_file():
        return f"Not a file: {p}"

    suffix = p.suffix.lower()

    try:
        raw = p.read_bytes()[:500_000].decode("utf-8", errors="replace")
    except PermissionError:
        return f"Permission denied: {p}"

    # JSON
    if suffix == ".json":
        try:
            data = json.loads(raw)
            return json.dumps(data, indent=2, ensure_ascii=False)[:8000]
        except json.JSONDecodeError as e:
            return f"JSON parse error: {e}\n\nRaw content:\n{raw[:2000]}"

    # YAML
    if suffix in (".yaml", ".yml"):
        try:
            import yaml
            data = yaml.safe_load(raw)
            return yaml.dump(data, default_flow_style=False, allow_unicode=True)[:8000]
        except Exception as e:
            return f"YAML parse error: {e}\n\nRaw content:\n{raw[:2000]}"

    # TOML
    if suffix == ".toml":
        try:
            import tomllib
            data = tomllib.loads(raw)
            return json.dumps(data, indent=2, ensure_ascii=False)[:8000]
        except Exception as e:
            return f"TOML parse error: {e}\n\nRaw content:\n{raw[:2000]}"

    # CSV
    if suffix == ".csv":
        reader = csv.DictReader(io.StringIO(raw))
        rows = []
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            rows.append(row)
        if not rows:
            return f"Empty CSV: {p}"
        headers = list(rows[0].keys())
        lines = [", ".join(headers)]
        lines += [", ".join(str(r.get(h, "")) for h in headers) for r in rows]
        total_note = f"\n... (showing {len(rows)} rows)" if len(rows) >= max_rows else ""
        return f"CSV: {p} ({len(headers)} columns)\n\n" + "\n".join(lines) + total_note

    # .env
    if p.name.startswith(".env") or suffix == ".env":
        result = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                result.append(line)
                continue
            if "=" in stripped:
                key, _, val = stripped.partition("=")
                # Redact anything that looks like a secret
                key_upper = key.strip().upper()
                if any(w in key_upper for w in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "PWD")):
                    result.append(f"{key.strip()}=<redacted>")
                else:
                    result.append(line)
            else:
                result.append(line)
        return "\n".join(result)

    # Fallback: plain text
    return raw[:8000]


def read_pdf(path: str, pages: str = "1-10") -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"File not found: {p}"

    return "PDF tooling disabled: PyMuPDF (AGPL-3.0) was removed for license compliance. Pending port to pdfium-render/lopdf."

    # Parse page range
    try:
        if "-" in pages:
            start_s, end_s = pages.split("-", 1)
            start_idx = int(start_s) - 1
            end_idx = int(end_s)
        else:
            start_idx = int(pages) - 1
            end_idx = int(pages)
        page_range = range(start_idx, end_idx)
    except ValueError:
        return f"Invalid page range: {pages}. Use '1-5' or '3'."

    try:
        with fitz.open(str(p)) as doc:
            total = len(doc)
            parts = [f"PDF: {p}  ({total} pages total)"]
            for i in page_range:
                if i < 0 or i >= total:
                    continue
                text = doc[i].get_text("text").strip()
                parts.append(f"\n--- Page {i + 1} ---\n{text}")
        # ponytail: 12000 was a flat cap regardless of requested range, so
        # bigger page requests returned the same truncated chunk — forcing
        # many small sequential read_pdf calls to cover a long document
        # (confirmed live: exhausted a 25-step budget on a 185-page report).
        # 30000 chars (~7-8k tokens) lets one call cover ~20-30 typical pages.
        result = "\n".join(parts)[:30000]
        # Scanned PDF? pymupdf's embedded-text extraction returns ~nothing for
        # image-only pages. Fall back to rendering pages and OCR-ing them via the
        # Unlimited-OCR daemon (tools/ocr.py handles the daemon->PaddleOCR chain).
        if _embedded_text_len(result) < 20:
            return _ocr_pdf_pages(str(p), start_idx, end_idx, total) or result
        return result
    except Exception as e:
        return f"PDF read error: {e}"


# Strip the "PDF: <path> (N pages total)" header and "--- Page N ---" separators
# so the scanned-PDF detector counts real text only.
_PDF_HEADER_RE = re.compile(r"^PDF: .*\((\d+) pages total\)$|^--- Page \d+ ---$", re.MULTILINE)


def _embedded_text_len(text: str) -> int:
    return len(re.sub(r"\s+", "", _PDF_HEADER_RE.sub("", text)))


def _ocr_pdf_pages(path: str, start: int, end: int, total: int) -> str:
    """Render a (scanned) PDF's pages to images and OCR them via tools.ocr."""
    import shutil
    import tempfile

    return "PDF OCR disabled: PyMuPDF (AGPL-3.0) was removed for license compliance."

    from tools import ocr as _ocr  # lazy — text PDFs never import the daemon client

    try:
        import fitz
        doc = fitz.open(path)
    except Exception as e:
        return f"PDF OCR failed: {e}"
    try:
        mat = fitz.Matrix(200 / 72, 200 / 72)
        tmp_dir = Path(tempfile.mkdtemp(prefix="pdf_ocr_"))
        parts = [f"PDF (OCR): {path}  ({total} pages total)"]
        for i in range(start, min(end, total)):
            pix = doc[i].get_pixmap(matrix=mat)
            img = tmp_dir / f"page_{i + 1:04d}.png"
            pix.save(str(img))
            page_text = _ocr.execute(str(img), lang="en")
            if page_text.startswith(("File not found", "Unsupported", "PaddleOCR is not installed", "OCR failed")):
                parts.append(f"\n--- Page {i + 1} ---\n[{page_text}]")
            else:
                parts.append(f"\n--- Page {i + 1} ---\n{page_text}")
        return "\n".join(parts)[:30000]
    except Exception as e:
        return f"PDF OCR failed: {e}"
    finally:
        try:
            doc.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


def parse_code(path: str, include_bodies: bool = False) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"File not found: {p}"

    try:
        source = p.read_text(errors="replace")
    except PermissionError:
        return f"Permission denied: {p}"

    suffix = p.suffix.lower()

    # Python — use AST for accurate parsing
    if suffix == ".py":
        return _parse_python(source, p, include_bodies)

    # JS/TS/other — regex-based extraction
    return _parse_generic(source, p, suffix)


def _parse_python(source: str, p: Path, include_bodies: bool) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return f"Syntax error in {p}: {e}"

    lines = source.splitlines()
    parts = [f"Python: {p}  ({len(lines)} lines)\n"]

    # Imports
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imports += [f"{mod}.{a.name}" for a in node.names]
    if imports:
        parts.append("Imports: " + ", ".join(imports[:20]))
        if len(imports) > 20:
            parts[-1] += f" ... (+{len(imports)-20} more)"

    # Top-level classes and functions
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parts.append(_fmt_func(node, lines, include_bodies, indent=""))
        elif isinstance(node, ast.ClassDef):
            parts.append(_fmt_class(node, lines, include_bodies))

    return "\n\n".join(parts)


def _fmt_func(node, lines, include_bodies, indent=""):
    doc = ast.get_docstring(node) or ""
    args = [a.arg for a in node.args.args]
    sig = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}({', '.join(args)})"
    result = f"{indent}{sig}"
    if doc:
        result += f"\n{indent}  \"\"\"{doc[:120]}\"\"\""
    if include_bodies:
        body_lines = lines[node.lineno - 1 : node.end_lineno]
        result += "\n" + "\n".join(f"{indent}  {l}" for l in body_lines[:30])
    return result


def _fmt_class(node, lines, include_bodies):
    doc = ast.get_docstring(node) or ""
    bases = [ast.unparse(b) for b in node.bases] if node.bases else []
    sig = f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
    result = sig
    if doc:
        result += f"\n  \"\"\"{doc[:120]}\"\"\""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result += "\n" + _fmt_func(child, lines, include_bodies, indent="  ")
    return result


def _parse_generic(source: str, p: Path, suffix: str) -> str:
    lines = source.splitlines()
    parts = [f"{suffix.lstrip('.').upper()} file: {p}  ({len(lines)} lines)\n"]

    # Functions
    func_re = re.compile(
        r"^(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s*)?\()",
        re.MULTILINE,
    )
    funcs = [m.group(1) or m.group(2) for m in func_re.finditer(source)]
    if funcs:
        parts.append("Functions: " + ", ".join(funcs))

    # Classes
    class_re = re.compile(r"^(?:export\s+)?class\s+(\w+)", re.MULTILINE)
    classes = class_re.findall(source)
    if classes:
        parts.append("Classes: " + ", ".join(classes))

    # Imports
    import_re = re.compile(r"^(?:import|require)\s+.+", re.MULTILINE)
    imports = import_re.findall(source)
    if imports:
        parts.append("Imports:\n" + "\n".join(imports[:10]))

    return "\n\n".join(parts)
