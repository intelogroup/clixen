# Inbox Monitor Job — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone Python job that runs every 5 minutes for 1 hour, monitoring Gmail for PDF attachments from two senders, processing them (PDF→MD, image extraction, summary), and delivering to Telegram + Google Tasks + Excel tracker.

**Architecture:** A self-contained script (`jobs/inbox_monitor_job.py`) runs a 12-tick loop (5 min × 12 = 1 hr). Each tick calls Gmail API to find new emails from the watched senders, downloads PDF attachments to `~/Downloads/agent/`, converts them to Markdown, extracts images, generates an LLM summary via Ollama, sends it to Telegram, optionally creates a Google Task, and logs everything to an Excel tracker. Seen message IDs are persisted in `jobs/seen_ids.json` to avoid reprocessing.

**Tech Stack:** Python 3.11+, `pymupdf` (fitz) for PDF→MD + image extraction, `openpyxl` for Excel, `google-api-python-client` (already installed), `requests`, `python-dotenv`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `tools-harness/tools/gmail.py` | Modify | Add `list_emails_with_attachments()` + `download_attachment()` |
| `tools-harness/tools/pdf_tools.py` | Create | PDF→Markdown conversion + image extraction |
| `tools-harness/tools/file_tracker.py` | Create | Excel tracker (create/append rows) |
| `tools-harness/jobs/__init__.py` | Create | Empty package marker |
| `tools-harness/jobs/inbox_monitor_job.py` | Create | Main orchestrator — 12-tick loop |
| `tools-harness/jobs/seen_ids.json` | Created at runtime | Persists processed message IDs |
| `tools-harness/tools/registry.py` | Modify | Register new tool schemas + executors |

---

## Task 1: Install dependencies

**Files:** none (shell only)

- [ ] **Step 1: Install pymupdf and openpyxl**

```bash
cd ~/Developer/clixen/tools-harness
pip install pymupdf openpyxl
```

Expected output: `Successfully installed pymupdf-... openpyxl-...`

- [ ] **Step 2: Verify fitz import**

```bash
python -c "import fitz; print(fitz.__version__)"
```

Expected: prints a version string like `1.24.x`

- [ ] **Step 3: Verify openpyxl import**

```bash
python -c "import openpyxl; print(openpyxl.__version__)"
```

Expected: prints a version string like `3.x.x`

---

## Task 2: Add Gmail attachment support

**Files:**
- Modify: `tools-harness/tools/gmail.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gmail_attachments.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools-harness'))
from unittest.mock import patch, MagicMock
from tools.gmail import list_emails_with_attachments, download_attachment

def test_list_emails_with_attachments_returns_list():
    mock_svc = MagicMock()
    mock_svc.users().messages().list().execute.return_value = {
        "messages": [{"id": "abc123"}]
    }
    mock_svc.users().messages().get().execute.return_value = {
        "id": "abc123",
        "payload": {
            "headers": [
                {"name": "From", "value": "jayveedz19@gmail.com"},
                {"name": "Subject", "value": "Test PDF"},
                {"name": "Date", "value": "Wed, 9 Apr 2026 10:00:00 +0000"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": ""}, "filename": ""},
                {"mimeType": "application/pdf", "filename": "doc.pdf",
                 "body": {"attachmentId": "att_001", "size": 50000}},
            ],
        },
    }
    with patch("tools.gmail._gmail_service", return_value=mock_svc):
        results = list_emails_with_attachments(
            senders=["jayveedz19@gmail.com"],
            seen_ids=set(),
        )
    assert len(results) == 1
    assert results[0]["message_id"] == "abc123"
    assert results[0]["attachments"][0]["filename"] == "doc.pdf"

def test_download_attachment_saves_file(tmp_path):
    mock_svc = MagicMock()
    import base64
    fake_bytes = b"%PDF-1.4 fake content"
    encoded = base64.urlsafe_b64encode(fake_bytes).decode()
    mock_svc.users().messages().attachments().get().execute.return_value = {
        "data": encoded
    }
    with patch("tools.gmail._gmail_service", return_value=mock_svc):
        dest = download_attachment(
            message_id="abc123",
            attachment_id="att_001",
            filename="doc.pdf",
            dest_dir=str(tmp_path),
        )
    assert dest.endswith("doc.pdf")
    assert (tmp_path / "doc.pdf").read_bytes() == fake_bytes
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd ~/Developer/clixen
python -m pytest tests/test_gmail_attachments.py -v 2>&1 | head -30
```

Expected: `ImportError` or `AttributeError` — functions don't exist yet.

- [ ] **Step 3: Add functions to gmail.py**

Open `tools-harness/tools/gmail.py`. Add after the `get_latest_email` function (before `send_email`):

```python
def list_emails_with_attachments(
    senders: list[str],
    seen_ids: set,
    max_results: int = 20,
) -> list[dict]:
    """
    Return emails from any of `senders` that have PDF attachments,
    excluding IDs already in `seen_ids`.

    Each result dict:
        message_id: str
        sender: str
        subject: str
        date: str
        attachments: list[{filename, attachment_id, size}]
    """
    svc = _gmail_service()
    if svc is None:
        return []

    query_parts = [f"from:{s}" for s in senders]
    query = f"({' OR '.join(query_parts)}) has:attachment filename:pdf"

    try:
        result = svc.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
    except Exception:
        return []

    messages = result.get("messages", [])
    output = []

    for m in messages:
        mid = m["id"]
        if mid in seen_ids:
            continue

        try:
            msg = svc.users().messages().get(
                userId="me", id=mid, format="full"
            ).execute()
        except Exception:
            continue

        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        attachments = _find_pdf_attachments(msg["payload"])
        if not attachments:
            continue

        output.append({
            "message_id": mid,
            "sender": headers.get("From", ""),
            "subject": headers.get("Subject", "(no subject)"),
            "date": headers.get("Date", ""),
            "attachments": attachments,
        })

    return output


def _find_pdf_attachments(payload: dict) -> list[dict]:
    """Recursively walk message parts, collect PDF attachment metadata."""
    results = []
    mime = payload.get("mimeType", "")
    filename = payload.get("filename", "")
    body = payload.get("body", {})

    if filename.lower().endswith(".pdf") and body.get("attachmentId"):
        results.append({
            "filename": filename,
            "attachment_id": body["attachmentId"],
            "size": body.get("size", 0),
        })

    for part in payload.get("parts", []):
        results.extend(_find_pdf_attachments(part))

    return results


def download_attachment(
    message_id: str,
    attachment_id: str,
    filename: str,
    dest_dir: str,
) -> str:
    """
    Download a Gmail attachment and save it to dest_dir/filename.
    Returns the absolute path of the saved file, or empty string on error.
    """
    import base64

    svc = _gmail_service()
    if svc is None:
        return ""

    try:
        att = svc.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
    except Exception as e:
        print(f"[gmail] download_attachment error: {e}")
        return ""

    data = base64.urlsafe_b64decode(att["data"] + "==")
    dest = Path(dest_dir).expanduser() / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return str(dest)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd ~/Developer/clixen
python -m pytest tests/test_gmail_attachments.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/Developer/clixen
git add tools-harness/tools/gmail.py tests/test_gmail_attachments.py
git commit -m "feat: add Gmail attachment listing and download"
```

---

## Task 3: Create PDF tools (PDF→MD + image extraction)

**Files:**
- Create: `tools-harness/tools/pdf_tools.py`
- Test: `tests/test_pdf_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pdf_tools.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools-harness'))

def test_pdf_to_markdown_returns_string(tmp_path):
    """Create a minimal real PDF and verify markdown output is a non-empty string."""
    import fitz
    from tools.pdf_tools import pdf_to_markdown

    # Create a minimal PDF with one text page
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello from test PDF")
    pdf_path = tmp_path / "sample.pdf"
    doc.save(str(pdf_path))
    doc.close()

    md_path, md_content = pdf_to_markdown(str(pdf_path), str(tmp_path))
    assert md_content.strip() != ""
    assert md_path.endswith(".md")
    assert os.path.exists(md_path)


def test_extract_pdf_images_empty_pdf(tmp_path):
    """A text-only PDF should return an empty image list."""
    import fitz
    from tools.pdf_tools import extract_pdf_images

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "No images here")
    pdf_path = tmp_path / "noimg.pdf"
    doc.save(str(pdf_path))
    doc.close()

    images = extract_pdf_images(str(pdf_path), str(tmp_path))
    assert images == []
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd ~/Developer/clixen
python -m pytest tests/test_pdf_tools.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'pdf_to_markdown' from 'tools.pdf_tools'`

- [ ] **Step 3: Create pdf_tools.py**

Create `tools-harness/tools/pdf_tools.py`:

```python
"""
PDF processing tools.

pdf_to_markdown  — convert a PDF file to Markdown, save alongside the PDF.
extract_pdf_images — extract embedded images from a PDF as PNG files.

Requires: pymupdf  (pip install pymupdf)
"""
from pathlib import Path


def pdf_to_markdown(pdf_path: str, output_dir: str | None = None) -> tuple[str, str]:
    """
    Convert a PDF to Markdown using PyMuPDF's text extraction.

    Saves a .md file next to the PDF (or in output_dir if given).
    Returns (md_file_path, md_content).
    """
    import fitz  # pymupdf

    p = Path(pdf_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"PDF not found: {p}")

    dest_dir = Path(output_dir).expanduser() if output_dir else p.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    md_path = dest_dir / (p.stem + ".md")

    doc = fitz.open(str(p))
    parts = [f"# {p.stem}\n"]

    for page_num, page in enumerate(doc, start=1):
        text = page.get_text("text").strip()
        if text:
            parts.append(f"\n## Page {page_num}\n\n{text}")

    doc.close()

    md_content = "\n".join(parts)
    md_path.write_text(md_content, encoding="utf-8")
    return str(md_path), md_content


def extract_pdf_images(pdf_path: str, output_dir: str) -> list[str]:
    """
    Extract all embedded raster images from a PDF.

    Saves each image as {pdf_stem}_img{N}.png in output_dir.
    Returns list of saved file paths (empty if no images).
    """
    import fitz  # pymupdf

    p = Path(pdf_path).expanduser().resolve()
    dest_dir = Path(output_dir).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(p))
    saved = []
    img_counter = 1

    for page in doc:
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue

            img_bytes = base_image.get("image", b"")
            if not img_bytes:
                continue

            # Always save as PNG regardless of source format
            img_fitz = fitz.Pixmap(doc, xref)
            if img_fitz.n > 4:  # CMYK → RGB
                img_fitz = fitz.Pixmap(fitz.csRGB, img_fitz)

            out_path = dest_dir / f"{p.stem}_img{img_counter}.png"
            img_fitz.save(str(out_path))
            saved.append(str(out_path))
            img_counter += 1

    doc.close()
    return saved
```

- [ ] **Step 4: Run tests**

```bash
cd ~/Developer/clixen
python -m pytest tests/test_pdf_tools.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/Developer/clixen
git add tools-harness/tools/pdf_tools.py tests/test_pdf_tools.py
git commit -m "feat: add PDF to markdown conversion and image extraction"
```

---

## Task 4: Create Excel file tracker

**Files:**
- Create: `tools-harness/tools/file_tracker.py`
- Test: `tests/test_file_tracker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_file_tracker.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools-harness'))

def test_tracker_creates_file_and_appends(tmp_path):
    from tools.file_tracker import update_tracker
    import openpyxl

    tracker_path = tmp_path / "tracker.xlsx"

    row1 = {
        "timestamp": "2026-04-09 10:00:00",
        "sender": "jayveedz19@gmail.com",
        "subject": "Report Q1",
        "filename": "report_q1.pdf",
        "pdf_path": "/Downloads/agent/report_q1.pdf",
        "md_path": "/Downloads/agent/report_q1.md",
        "page_count": 5,
        "image_count": 2,
        "file_size_kb": 120,
        "summary": "Q1 financial report",
        "task_created": False,
        "telegram_sent": True,
    }
    update_tracker(str(tracker_path), row1)
    assert tracker_path.exists()

    wb = openpyxl.load_workbook(str(tracker_path))
    ws = wb.active
    assert ws.max_row == 2  # header + 1 data row
    assert ws.cell(row=2, column=2).value == "jayveedz19@gmail.com"

    # Append second row
    row2 = dict(row1)
    row2["sender"] = "kalinovjim@gmail.com"
    update_tracker(str(tracker_path), row2)

    wb2 = openpyxl.load_workbook(str(tracker_path))
    ws2 = wb2.active
    assert ws2.max_row == 3


def test_tracker_creates_directory(tmp_path):
    from tools.file_tracker import update_tracker
    nested = tmp_path / "a" / "b" / "tracker.xlsx"
    update_tracker(str(nested), {"timestamp": "2026-04-09", "sender": "x@x.com",
        "subject": "", "filename": "f.pdf", "pdf_path": "", "md_path": "",
        "page_count": 1, "image_count": 0, "file_size_kb": 10,
        "summary": "", "task_created": False, "telegram_sent": False})
    assert nested.exists()
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd ~/Developer/clixen
python -m pytest tests/test_file_tracker.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'update_tracker'`

- [ ] **Step 3: Create file_tracker.py**

Create `tools-harness/tools/file_tracker.py`:

```python
"""
Excel file tracker for the inbox monitor job.

Maintains agent_tracker.xlsx (or any path) with one row per processed PDF.
Creates the file and directory if they don't exist.

Requires: openpyxl  (pip install openpyxl)
"""
from pathlib import Path

_COLUMNS = [
    "Timestamp",
    "Sender",
    "Subject",
    "Filename",
    "PDF Path",
    "MD Path",
    "Page Count",
    "Image Count",
    "File Size (KB)",
    "Summary",
    "Task Created",
    "Telegram Sent",
]

_ROW_KEYS = [
    "timestamp",
    "sender",
    "subject",
    "filename",
    "pdf_path",
    "md_path",
    "page_count",
    "image_count",
    "file_size_kb",
    "summary",
    "task_created",
    "telegram_sent",
]


def update_tracker(tracker_path: str, row: dict) -> None:
    """
    Append a row to the Excel tracker.
    Creates the file (with header) if it doesn't exist.

    row keys: timestamp, sender, subject, filename, pdf_path, md_path,
              page_count, image_count, file_size_kb, summary,
              task_created, telegram_sent
    """
    import openpyxl

    p = Path(tracker_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists():
        wb = openpyxl.load_workbook(str(p))
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "PDF Inbox Log"
        ws.append(_COLUMNS)
        # Bold the header row
        from openpyxl.styles import Font
        for cell in ws[1]:
            cell.font = Font(bold=True)

    ws.append([row.get(k, "") for k in _ROW_KEYS])
    wb.save(str(p))
```

- [ ] **Step 4: Run tests**

```bash
cd ~/Developer/clixen
python -m pytest tests/test_file_tracker.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/Developer/clixen
git add tools-harness/tools/file_tracker.py tests/test_file_tracker.py
git commit -m "feat: add Excel file tracker for processed PDFs"
```

---

## Task 5: Create the main job orchestrator

**Files:**
- Create: `tools-harness/jobs/__init__.py`
- Create: `tools-harness/jobs/inbox_monitor_job.py`

- [ ] **Step 1: Create package marker**

Create `tools-harness/jobs/__init__.py` as an empty file.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_inbox_monitor_job.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools-harness'))

def test_load_seen_ids_empty(tmp_path):
    from jobs.inbox_monitor_job import load_seen_ids, save_seen_ids
    p = tmp_path / "seen.json"
    ids = load_seen_ids(str(p))
    assert ids == set()

def test_save_and_reload_seen_ids(tmp_path):
    from jobs.inbox_monitor_job import load_seen_ids, save_seen_ids
    p = tmp_path / "seen.json"
    save_seen_ids({"abc", "def"}, str(p))
    loaded = load_seen_ids(str(p))
    assert loaded == {"abc", "def"}

def test_ollama_summarize_fallback():
    """If Ollama is unreachable, summarize falls back to truncation."""
    from jobs.inbox_monitor_job import summarize_text
    long_text = "A" * 2000
    result = summarize_text(long_text, model="qwen3:4b", timeout=0.001)
    assert isinstance(result, str)
    assert len(result) > 0
```

- [ ] **Step 3: Run test to confirm it fails**

```bash
cd ~/Developer/clixen
python -m pytest tests/test_inbox_monitor_job.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'jobs'`

- [ ] **Step 4: Create inbox_monitor_job.py**

Create `tools-harness/jobs/inbox_monitor_job.py`:

```python
"""
Inbox Monitor Job
=================
Runs every 5 minutes for 1 hour (12 ticks).
Watches Gmail for PDF attachments from watched senders.
Per PDF: download → PDF→MD → extract images → LLM summary → Telegram → Tasks → tracker.

Run:
    cd tools-harness
    python -m jobs.inbox_monitor_job

Or one-shot (no loop):
    python -m jobs.inbox_monitor_job --once
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Load .env from project root
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Add tools-harness to path for tool imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.gmail import list_emails_with_attachments, download_attachment
from tools.pdf_tools import pdf_to_markdown, extract_pdf_images
from tools.file_tracker import update_tracker
from tools.telegram_send import send_telegram
from tools.gtasks import create_task

# ── Configuration ──────────────────────────────────────────────────────────────

WATCHED_SENDERS = ["jayveedz19@gmail.com", "kalinovjim@gmail.com"]
AGENT_DIR = Path.home() / "Downloads" / "agent"
TRACKER_PATH = AGENT_DIR / "agent_tracker.xlsx"
SEEN_IDS_PATH = Path(__file__).parent / "seen_ids.json"

TICK_INTERVAL_SEC = 5 * 60   # 5 minutes
TOTAL_TICKS = 12              # 12 × 5 min = 1 hour

OLLAMA_URL = "http://localhost:11434/api/chat"
SUMMARY_MODEL = "qwen3:4b"

# ── State helpers ───────────────────────────────────────────────────────────────

def load_seen_ids(path: str | None = None) -> set:
    p = Path(path or SEEN_IDS_PATH)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text()))
    except Exception:
        return set()


def save_seen_ids(ids: set, path: str | None = None) -> None:
    p = Path(path or SEEN_IDS_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(ids)))


# ── LLM summary ────────────────────────────────────────────────────────────────

def summarize_text(text: str, model: str = SUMMARY_MODEL, timeout: float = 30.0) -> str:
    """
    Ask Ollama to summarize text in 3-5 bullet points.
    Falls back to first 500 chars if Ollama is unreachable or times out.
    """
    import requests

    prompt = (
        "Summarize the following document in 3-5 concise bullet points. "
        "Focus on key facts, decisions, or action items.\n\n"
        f"{text[:6000]}"
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": 300, "temperature": 0.3},
            },
            timeout=timeout,
        )
        if resp.ok:
            return resp.json()["message"]["content"].strip()
    except Exception:
        pass

    # Fallback: first 500 chars of the text
    return text[:500].strip() + ("..." if len(text) > 500 else "")


# ── Task creation heuristic ─────────────────────────────────────────────────────

def _needs_task(summary: str) -> bool:
    """Return True if the summary contains action item keywords."""
    keywords = [
        "action", "todo", "follow up", "deadline", "due", "must", "need to",
        "please", "required", "urgent", "asap", "send", "review", "approve",
        "confirm", "schedule", "meeting", "respond",
    ]
    lower = summary.lower()
    return any(kw in lower for kw in keywords)


# ── Per-PDF pipeline ────────────────────────────────────────────────────────────

def process_pdf(
    message_id: str,
    sender: str,
    subject: str,
    attachment: dict,
) -> dict:
    """
    Full pipeline for one PDF attachment.
    Returns a tracker row dict.
    """
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    filename = attachment["filename"]
    attachment_id = attachment["attachment_id"]
    size_kb = attachment["size"] // 1024

    print(f"  [download] {filename} from {sender}")
    pdf_path = download_attachment(
        message_id=message_id,
        attachment_id=attachment_id,
        filename=filename,
        dest_dir=str(AGENT_DIR),
    )
    if not pdf_path:
        print(f"  [error] download failed for {filename}")
        return {}

    print(f"  [convert] PDF → Markdown")
    try:
        md_path, md_content = pdf_to_markdown(pdf_path, str(AGENT_DIR))
    except Exception as e:
        print(f"  [error] pdf_to_markdown: {e}")
        md_path, md_content = "", ""

    print(f"  [images] extracting")
    try:
        image_paths = extract_pdf_images(pdf_path, str(AGENT_DIR))
    except Exception as e:
        print(f"  [error] extract_pdf_images: {e}")
        image_paths = []

    # Page count from fitz
    page_count = 0
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page_count = len(doc)
        doc.close()
    except Exception:
        pass

    print(f"  [summarize] via {SUMMARY_MODEL}")
    summary = summarize_text(md_content or "(no text extracted)")

    # Telegram
    telegram_sent = False
    telegram_msg = (
        f"New PDF from {sender}\n"
        f"Subject: {subject}\n"
        f"File: {filename} ({page_count} pages, {len(image_paths)} images)\n\n"
        f"{summary}"
    )
    try:
        result = send_telegram(telegram_msg)
        telegram_sent = "sent" in result.lower()
        print(f"  [telegram] {result}")
    except Exception as e:
        print(f"  [telegram error] {e}")

    # Google Task (only if action items detected)
    task_created = False
    if _needs_task(summary):
        try:
            task_title = f"Follow up: {subject} (from {sender})"
            result = create_task(title=task_title, notes=summary[:500])
            task_created = "created" in result.lower() or "task" in result.lower()
            print(f"  [task] {result}")
        except Exception as e:
            print(f"  [task error] {e}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = {
        "timestamp": now,
        "sender": sender,
        "subject": subject,
        "filename": filename,
        "pdf_path": pdf_path,
        "md_path": md_path,
        "page_count": page_count,
        "image_count": len(image_paths),
        "file_size_kb": size_kb,
        "summary": summary[:200],
        "task_created": task_created,
        "telegram_sent": telegram_sent,
    }

    try:
        update_tracker(str(TRACKER_PATH), row)
        print(f"  [tracker] row written to {TRACKER_PATH}")
    except Exception as e:
        print(f"  [tracker error] {e}")

    return row


# ── Tick ────────────────────────────────────────────────────────────────────────

def run_tick(seen_ids: set) -> set:
    """
    One check cycle: find new emails with PDFs, process each, return updated seen_ids.
    """
    print(f"\n[tick] {datetime.now().isoformat()} — checking inbox")
    emails = list_emails_with_attachments(
        senders=WATCHED_SENDERS,
        seen_ids=seen_ids,
    )

    if not emails:
        print("[tick] no new PDFs")
        return seen_ids

    for email in emails:
        mid = email["message_id"]
        seen_ids.add(mid)
        for att in email["attachments"]:
            process_pdf(
                message_id=mid,
                sender=email["sender"],
                subject=email["subject"],
                attachment=att,
            )

    return seen_ids


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one tick and exit")
    args = parser.parse_args()

    seen_ids = load_seen_ids()
    print(f"[job] Inbox Monitor started. Loaded {len(seen_ids)} seen IDs.")
    print(f"[job] Watching: {WATCHED_SENDERS}")
    print(f"[job] Agent dir: {AGENT_DIR}")

    if args.once:
        seen_ids = run_tick(seen_ids)
        save_seen_ids(seen_ids)
        return

    # 12 ticks × 5 minutes = 1 hour
    for tick in range(1, TOTAL_TICKS + 1):
        print(f"\n{'='*50}")
        print(f"[job] Tick {tick}/{TOTAL_TICKS}")
        seen_ids = run_tick(seen_ids)
        save_seen_ids(seen_ids)

        if tick < TOTAL_TICKS:
            print(f"[job] Sleeping {TICK_INTERVAL_SEC // 60} min until next tick...")
            time.sleep(TICK_INTERVAL_SEC)

    print("\n[job] 1-hour run complete.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests**

```bash
cd ~/Developer/clixen
python -m pytest tests/test_inbox_monitor_job.py -v
```

Expected: `3 passed`

- [ ] **Step 6: Smoke test (dry run — one tick, no real emails required)**

```bash
cd ~/Developer/clixen/tools-harness
python -m jobs.inbox_monitor_job --once 2>&1 | head -20
```

Expected: `[job] Inbox Monitor started. ...` and `[tick] ... — checking inbox` and `[tick] no new PDFs` (or processes real emails if any exist)

- [ ] **Step 7: Commit**

```bash
cd ~/Developer/clixen
git add tools-harness/jobs/__init__.py tools-harness/jobs/inbox_monitor_job.py \
    tests/test_inbox_monitor_job.py
git commit -m "feat: add inbox monitor job — 5-min polling loop for PDF emails"
```

---

## Task 6: Register new tools in registry (for LLM agent access)

**Files:**
- Modify: `tools-harness/tools/registry.py`

The LLM can now trigger the pipeline or check for attachments via tool calls.

- [ ] **Step 1: Add schemas to registry.py**

Open `tools-harness/tools/registry.py`. After the Gmail imports block, add:

```python
from tools.pdf_tools import pdf_to_markdown, extract_pdf_images
from tools.gmail import list_emails_with_attachments, download_attachment
```

Add these schemas after `GET_LATEST_EMAIL_SCHEMA`:

```python
LIST_ATTACHMENTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_pdf_attachments",
        "description": (
            "List recent emails from watched senders (jayveedz19@gmail.com, kalinovjim@gmail.com) "
            "that contain PDF attachments. Returns sender, subject, date, and attachment filenames."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "senders": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of sender email addresses to filter by",
                    "default": ["jayveedz19@gmail.com", "kalinovjim@gmail.com"],
                },
            },
            "required": [],
        },
    },
}

CONVERT_PDF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "convert_pdf",
        "description": (
            "Convert a local PDF file to Markdown and extract any embedded images as PNG files. "
            "Saves .md and _imgN.png files alongside the PDF. Returns the markdown text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pdf_path": {
                    "type": "string",
                    "description": "Absolute path to the PDF file",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Directory to save output files (defaults to same dir as PDF)",
                    "default": "",
                },
            },
            "required": ["pdf_path"],
        },
    },
}
```

- [ ] **Step 2: Add executors to EXECUTORS dict**

In the EXECUTORS dict (after the `get_latest_email` entry), add:

```python
    "list_pdf_attachments": lambda args: json.dumps(
        list_emails_with_attachments(
            senders=args.get("senders", ["jayveedz19@gmail.com", "kalinovjim@gmail.com"]),
            seen_ids=set(),
        ),
        default=str,
    ),
    "convert_pdf": lambda args: _convert_pdf_executor(args),
```

And add the helper after EXECUTORS:

```python
def _convert_pdf_executor(args: dict) -> str:
    import json
    pdf_path = args["pdf_path"]
    output_dir = args.get("output_dir") or None
    md_path, md_content = pdf_to_markdown(pdf_path, output_dir)
    images = extract_pdf_images(pdf_path, output_dir or str(Path(pdf_path).parent))
    return (
        f"Converted: {md_path}\n"
        f"Images extracted: {len(images)} ({', '.join(Path(i).name for i in images[:5])})\n\n"
        f"{md_content[:3000]}"
    )
```

- [ ] **Step 3: Add schemas to ALL_TOOLS list**

In the `ALL_TOOLS` list, after the Gmail schemas block, add:

```python
    LIST_ATTACHMENTS_SCHEMA,
    CONVERT_PDF_SCHEMA,
```

- [ ] **Step 4: Smoke test registry loads**

```bash
cd ~/Developer/clixen/tools-harness
python -c "from tools.registry import ALL_TOOLS; names = [t['function']['name'] for t in ALL_TOOLS]; print([n for n in names if 'pdf' in n or 'attach' in n])"
```

Expected: `['list_pdf_attachments', 'convert_pdf']` (may include `read_pdf` too)

- [ ] **Step 5: Commit**

```bash
cd ~/Developer/clixen
git add tools-harness/tools/registry.py
git commit -m "feat: register list_pdf_attachments and convert_pdf in tool registry"
```

---

## Task 7: Run the full job

- [ ] **Step 1: Start the 1-hour job in background**

```bash
cd ~/Developer/clixen/tools-harness
nohup python -m jobs.inbox_monitor_job > /tmp/inbox_monitor.log 2>&1 &
echo "Job PID: $!"
```

- [ ] **Step 2: Monitor the first tick**

```bash
tail -f /tmp/inbox_monitor.log
```

Watch for `[tick]` lines. If PDFs are found, you'll see download → convert → summarize → telegram → tracker steps.

- [ ] **Step 3: Verify agent dir created**

```bash
ls ~/Downloads/agent/
```

Expected: `agent_tracker.xlsx` exists (created even on a tick with no emails).

- [ ] **Step 4: Stop job if needed**

```bash
kill $(cat /tmp/inbox_monitor.pid 2>/dev/null) 2>/dev/null || pkill -f inbox_monitor_job
```

---

## Self-Review

**Spec coverage:**
- Every 5 min for 1 hr: tick loop in Task 5 (12 × 300s) ✓
- Read inbox from jayveedz19 + kalinovjim: `WATCHED_SENDERS` + `list_emails_with_attachments` ✓
- Save PDFs to ~/Downloads/agent (create if missing): `AGENT_DIR.mkdir(parents=True, exist_ok=True)` + `download_attachment` ✓
- Convert PDF → MD: `pdf_to_markdown` in Task 3 ✓
- Extract images as imgN.png: `extract_pdf_images` saves `{stem}_img{N}.png` ✓
- Send Telegram summary: `send_telegram(telegram_msg)` in `process_pdf` ✓
- Create Google Task if needed: `_needs_task(summary)` heuristic + `create_task` ✓
- Excel tracker with metadata: `update_tracker` in Task 4 ✓

**No placeholders:** all code blocks are complete.

**Type consistency:** `list_emails_with_attachments` returns `list[dict]` with keys `message_id`, `sender`, `subject`, `date`, `attachments` — used identically in `run_tick` and in the registry executor.
