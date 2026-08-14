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
from email import policy as _email_policy
from email.parser import BytesParser as _BytesParser
from html.parser import HTMLParser as _HTMLParser
import io
import json
import re
import shutil
import subprocess
import tempfile
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
            "Extract text from a PDF file as markdown (whole document in one pass). "
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
                        "Unused — kept for compatibility. The whole document is always "
                        "converted; output is capped at 30000 chars total."
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
_ZIP_MEMBER_LIMIT = 200
_ZIP_MEMBER_BYTES = 25 * 1024 * 1024
_ZIP_TOTAL_BYTES = 100 * 1024 * 1024
_DOCUMENT_SUFFIXES = _IMAGE_SUFFIXES | {
    ".pdf", ".docx", ".xlsx", ".pptx", ".rtf", ".html", ".htm", ".eml",
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".toml",
}
_UNSUPPORTED_BINARY_SUFFIXES = {
    ".doc", ".xls", ".ppt", ".key", ".numbers", ".msg", ".mp3", ".wav", ".m4a",
    ".mp4", ".mov", ".avi", ".mkv", ".flac",
}


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
    elif suffix == ".eml":
        text = _read_eml(p, max_chars=max_chars)
    elif suffix in (".pptx", ".ppt"):
        text = _read_pptx(p, max_chars=max_chars)
    elif suffix == ".pages":
        text = _read_pages(p, max_chars=max_chars)
    elif suffix in _STRUCTURED_SUFFIXES:
        text = parse_file(str(p))[:max_chars]
    elif suffix == ".zip":
        text = _read_zip(p, max_chars=max_chars)
    elif suffix == ".rtf":
        text = _read_rtf(p, max_chars=max_chars)
    elif suffix in _CODE_SUFFIXES:
        text = parse_code(str(p), include_bodies=False)[:max_chars]
    elif suffix in _IMAGE_SUFFIXES:
        try:
            from tools.ocr import execute as ocr_execute
        except Exception as e:
            return f"OCR unavailable for {p}: {e}"
        text = ocr_execute(str(p), lang=lang)[:max_chars]
    elif suffix in (".html", ".htm"):
        text = _read_html(p, max_chars=max_chars)
    elif suffix in _UNSUPPORTED_BINARY_SUFFIXES:
        return (
            f"Unsupported document format: {suffix}\nPath: {p}\n"
            "Convert/export it to PDF, DOCX, XLSX, PPTX, RTF, or plain text first."
        )
    elif suffix in _TEXT_SUFFIXES or _looks_like_text(p):
        # A first-page-only read hides conclusions, appendices, and late-file
        # values. Keep head and tail so one bounded extraction can see both
        # document setup and terminal evidence without an extra model round.
        try:
            raw_text = p.read_text(errors="replace")
            if len(raw_text) <= max_chars:
                text = raw_text
            else:
                head_chars = max_chars // 2
                tail_chars = max_chars - head_chars
                text = (
                    raw_text[:head_chars]
                    + f"\n\n[... middle omitted: {len(raw_text) - max_chars} characters ...]\n\n"
                    + raw_text[-tail_chars:]
                )
        except Exception:
            try:
                from tools.filesystem import read_file
                text = read_file(str(p), limit=max(400, max_chars // 80))[:max_chars]
            except Exception:
                text = ""
    else:
        return (
            f"Unsupported document format: {p.suffix or '(no extension)'}\n"
            f"Path: {p}\n"
            "Supported: text/code files, JSON/YAML/TOML/CSV/.env, PDF, DOCX, XLSX, PPTX, ZIP, "
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


def _docx_comments_markdown_safe(path: Path) -> str:
    try:
        from tools.office_tools import _docx_comments_markdown
        return _docx_comments_markdown(str(path))
    except Exception:
        return ""


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
                            if elem.tag.endswith("}p"):
                                style = ""
                                for paragraph_property in elem:
                                    if paragraph_property.tag.endswith("}pPr"):
                                        for style_node in paragraph_property:
                                            if style_node.tag.endswith("}pStyle"):
                                                style = style_node.attrib.get(
                                                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val",
                                                    "",
                                                )
                                match = re.fullmatch(r"Heading([1-6])", style, re.I)
                                if match:
                                    text = "#" * int(match.group(1)) + " " + text
                            lines.append(text)
                if lines:
                    parts.append("\n".join(lines))
            body = "\n\n".join(parts).strip()
            comments_md = _docx_comments_markdown_safe(path)
            if not body and not comments_md:
                return f"No text found in DOCX: {path}"

            if comments_md:
                # Reserve room for comments so they survive truncation of long bodies.
                body_budget = max(0, max_chars - len(comments_md) - 2)
                result = f"{body[:body_budget]}\n\n{comments_md}"
            else:
                result = body
            return result[:max_chars]
    except zipfile.BadZipFile:
        return f"DOCX read error: not a valid .docx zip file: {path}"
    except Exception as e:
        return f"DOCX read error: {e}"


class _TextHTMLParser(_HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def _read_html(path: Path, max_chars: int) -> str:
    parser = _TextHTMLParser()
    parser.feed(path.read_text(errors="replace"))
    return "\n".join(parser.parts)[:max_chars]


def _read_rtf(path: Path, max_chars: int) -> str:
    """Extract readable text from common RTF without executing embedded content."""
    raw = path.read_text(errors="replace")
    raw = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", errors="replace"), raw)
    raw = raw.replace(r"\par", "\n").replace(r"\line", "\n").replace(r"\tab", "\t")
    raw = re.sub(r"\\u-?\d+\??", "", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", raw)
    raw = raw.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
    raw = raw.replace("{", "").replace("}", "")
    return re.sub(r"\n{3,}", "\n\n", raw).strip()[:max_chars]


def _read_eml(path: Path, max_chars: int) -> str:
    message = _BytesParser(policy=_email_policy.default).parsebytes(path.read_bytes())
    parts = [f"Subject: {message.get('subject', '')}"]
    parts.append(f"From: {message.get('from', '')}")
    parts.append(f"To: {message.get('to', '')}")
    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        parts.append(body.get_content())
    return "\n".join(part.strip() for part in parts if part and part.strip())[:max_chars]


def _read_pptx(path: Path, max_chars: int) -> str:
    import anydoc

    try:
        text = anydoc.to_markdown(str(path))
    except Exception as e:
        return f"PPTX read error: {e}"
    return (f"PPTX: {path}\n\n{text}" if text.strip() else f"No text found in PPTX: {path}")[:max_chars]


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

    total = _pdf_page_count(p)
    start, end = _parse_page_range(pages, total)
    text = _pdftotext_pages(p, start, end)
    if _embedded_text_len(text) >= 20:
        return f"PDF: {p} ({total} pages total)\n\n{text}"[:30000]
    ocr = _ocr_pdf_pages(str(p), start, end, total)
    if ocr:
        return ocr[:30000]
    return f"PDF read error: no extractable text in selected pages {start}-{end}"


def _pdf_page_count(path: Path) -> int:
    binary = shutil.which("pdfinfo")
    if binary:
        result = subprocess.run([binary, str(path)], capture_output=True, text=True, check=False)
        match = re.search(r"^Pages:\s*(\d+)", result.stdout, re.MULTILINE)
        if match:
            return int(match.group(1))
    return 1


def _parse_page_range(spec: str, total: int) -> tuple[int, int]:
    """Parse a bounded one-based page range; default is the first ten pages."""
    total = max(1, int(total or 1))
    raw = str(spec or "1-10").strip().lower()
    if raw in {"all", "*"}:
        return 1, total
    first = last = None
    match = re.fullmatch(r"(\d+)(?:\s*[-:]\s*(\d+))?", raw)
    if match:
        first = int(match.group(1))
        last = int(match.group(2) or first)
    if first is None or first < 1 or last < first:
        raise ValueError(f"invalid PDF page range: {spec!r}")
    return min(first, total), min(last, total)


def _pdftotext_pages(path: Path, start: int, end: int) -> str:
    binary = shutil.which("pdftotext")
    if not binary:
        return ""
    result = subprocess.run(
        [binary, "-f", str(start), "-l", str(end), "-layout", str(path), "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    pages = result.stdout.split("\f")
    rendered = []
    for offset, page in enumerate(pages[: end - start + 1]):
        index = start + offset
        body = page.strip()
        if body:
            rendered.append(f"--- Page {index} ---\n{body}")
    return "\n\n".join(rendered)


# Strip the "PDF: <path> (N pages total)" header and "--- Page N ---" separators
# so the scanned-PDF detector counts real text only.
_PDF_HEADER_RE = re.compile(r"^PDF: .*\((\d+) pages total\)$|^--- Page \d+ ---$", re.MULTILINE)


def _embedded_text_len(text: str) -> int:
    return len(re.sub(r"\s+", "", _PDF_HEADER_RE.sub("", text)))


def _ocr_pdf_pages(path: str, start: int, end: int, total: int) -> str:
    """Render selected scanned-PDF pages with Poppler and OCR each page."""
    from tools import ocr as _ocr

    binary = shutil.which("pdftoppm")
    if not binary or not start or not end:
        return ""
    tmp_dir = Path(tempfile.mkdtemp(prefix="pdf_ocr_"))
    try:
        subprocess.run(
            [binary, "-f", str(start), "-l", str(end), "-png", "-r", "200", path, str(tmp_dir / "page")],
            capture_output=True,
            check=True,
        )
        parts = [f"PDF: {path} ({total} pages total)"]
        images = sorted(tmp_dir.glob("page-*.png"))
        for index, image in enumerate(images, start):
            page_text = _ocr.execute(str(image), lang="en")
            parts.append(f"--- Page {index} ---\n{page_text}")
        return "\n\n".join(parts)
    except Exception:
        return ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _read_zip(path: Path, max_chars: int) -> str:
    """Inspect and selectively extract a ZIP without path traversal or bombs."""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return f"ZIP read error: {exc}"
    try:
        members = archive.infolist()
        if len(members) > _ZIP_MEMBER_LIMIT:
            return f"ZIP rejected: {len(members)} members exceeds limit {_ZIP_MEMBER_LIMIT}"
        total_size = sum(max(0, item.file_size) for item in members)
        if total_size > _ZIP_TOTAL_BYTES:
            return f"ZIP rejected: {total_size} uncompressed bytes exceeds limit {_ZIP_TOTAL_BYTES}"
        lines = [f"ZIP: {path} ({len(members)} members, {total_size} uncompressed bytes)"]
        with tempfile.TemporaryDirectory(prefix="zip_docs_") as temp:
            for index, item in enumerate(members):
                name = item.filename.replace("\\", "/")
                candidate = Path(name)
                if candidate.is_absolute() or ".." in candidate.parts:
                    lines.append(f"- {name}: skipped unsafe path")
                    continue
                if item.is_dir():
                    continue
                if item.file_size > _ZIP_MEMBER_BYTES:
                    lines.append(f"- {name}: skipped member over size limit")
                    continue
                suffix = Path(name).suffix.lower()
                lines.append(f"- {name} ({item.file_size} bytes)")
                if suffix not in _DOCUMENT_SUFFIXES or sum(len(line) for line in lines) >= max_chars:
                    continue
                payload = archive.read(item)
                member_path = Path(temp) / f"member_{index}{suffix}"
                member_path.write_bytes(payload)
                extracted = read_document(str(member_path), max_chars=min(3500, max_chars))
                extracted = extracted.replace(str(member_path), f"{path}::{name}")
                lines.append(f"[Member {name}]\n{extracted}")
        return "\n\n".join(lines)[:max_chars]
    finally:
        archive.close()

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
