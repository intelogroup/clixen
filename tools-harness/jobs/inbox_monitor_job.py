"""
Inbox Monitor Job
=================
Runs every 5 minutes for 1 hour (12 ticks).
Watches Gmail for PDF, Excel, and Word attachments from watched senders.
Per attachment: download → convert → LLM summary → Telegram → Tasks → tracker.

Configuration (via .env):
    WATCHED_SENDERS   Comma-separated list of sender email addresses to watch.
                      Defaults to the two hardcoded addresses below if unset.

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
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Add tools-harness to path for tool imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from clients.cloud_client import chat as cloud_chat, DEFAULT_CLOUD_MODEL
from tools.gmail import list_emails_with_attachments, download_attachment
from tools.pdf_tools import pdf_to_markdown, extract_pdf_images
from tools.office_tools import excel_to_markdown, excel_metadata, docx_to_markdown, PasswordProtectedError
from tools.file_tracker import update_tracker
from tools.telegram_send import send_telegram
from tools.gtasks import create_task
from tools.notifications import push as notify
from jobs import job_queue
# ── Configuration ──────────────────────────────────────────────────────────────

_SUPPRESS_TELEGRAM = False

_DEFAULT_SENDERS = [
    "kalinovjim@gmail.com",
]

def _load_watched_senders() -> list[str]:
    """
    Load sender list from WATCHED_SENDERS env var (comma-separated).
    Falls back to _DEFAULT_SENDERS if the variable is absent or empty.
    """
    raw = os.environ.get("WATCHED_SENDERS", "").strip()
    if not raw:
        return list(_DEFAULT_SENDERS)
    return [s.strip() for s in raw.split(",") if s.strip()]

WATCHED_SENDERS: list[str] = _load_watched_senders()

AGENT_DIR = Path.home() / "Downloads" / "agent"
TRACKER_PATH = AGENT_DIR / "agent_tracker.xlsx"

TICK_INTERVAL_SEC = 5 * 60   # 5 minutes
TOTAL_TICKS = 12              # 12 × 5 min = 1 hour

SUMMARY_MODEL = DEFAULT_CLOUD_MODEL

# ── State helpers ───────────────────────────────────────────────────────────────

def load_seen_ids() -> set:
    """Load seen message IDs from SQLite (authoritative) with JSON fallback for migration."""
    ids = job_queue.load_seen_ids()
    if ids:
        return ids
    # One-time migration: if seen_ids.json still exists, import it then remove it
    legacy = Path(__file__).parent / "seen_ids.json"
    if legacy.exists():
        try:
            legacy_ids = set(json.loads(legacy.read_text()))
            for mid in legacy_ids:
                job_queue.save_seen_id(mid)
            legacy.rename(legacy.with_suffix(".json.migrated"))
            print(f"[inbox_monitor] migrated {len(legacy_ids)} seen IDs from JSON to SQLite")
            return legacy_ids
        except Exception as e:
            print(f"[inbox_monitor] migration warning: {e}")
    return set()


def save_seen_ids(ids: set) -> None:
    """Persist any new seen IDs to SQLite. (Legacy JSON path no longer used.)"""
    for mid in ids:
        job_queue.save_seen_id(mid)


# ── LLM summary ────────────────────────────────────────────────────────────────

def summarize_text(text: str, model: str = SUMMARY_MODEL) -> str:
    """
    Ask cloud model to summarize text in 3-5 bullet points.
    Falls back to first 500 chars on failure.
    """
    prompt = (
        "Summarize the following document in 3-5 concise bullet points. "
        "Focus on key facts, decisions, or action items.\n\n"
        f"{text[:6000]}"
    )
    try:
        return cloud_chat(prompt, tools=[], max_rounds=1, model=model, bypass_budget=True)
    except Exception:
        pass

    return text[:500].strip() + "..."


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


# ── Per-attachment pipeline helpers ────────────────────────────────────────────

def _send_and_track(
    *,
    file_path: str,
    converted_path: str,
    file_type: str,
    sender: str,
    subject: str,
    filename: str,
    size_kb: int,
    extra_meta: str,
    summary: str,
) -> dict:
    """
    Common tail of every attachment pipeline:
    send Telegram notification, optionally create a Google Task, write tracker row.
    Returns the tracker row dict.
    """
    telegram_sent = False
    telegram_msg = (
        f"New {file_type.upper()} from {sender}\n"
        f"Subject: {subject}\n"
        f"File: {filename} ({extra_meta})\n\n"
        f"{summary}"
    )
    try:
        if not _SUPPRESS_TELEGRAM:
            result = send_telegram(telegram_msg)
            telegram_sent = "sent" in result.lower()
            print(f"  [telegram] {result}")
    except Exception as e:
        print(f"  [telegram error] {e}")

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
        "file_type": file_type,
        "file_path": file_path,
        "converted_path": converted_path,
        "page_count": "",
        "image_count": "",
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


# ── Per-type pipeline functions ─────────────────────────────────────────────────

def process_pdf(message_id: str, sender: str, subject: str, attachment: dict) -> dict:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    filename = attachment["filename"]
    size_kb = attachment["size"] // 1024

    print(f"  [download] {filename} from {sender}")
    file_path = download_attachment(
        message_id=message_id,
        attachment_id=attachment["attachment_id"],
        filename=filename,
        dest_dir=str(AGENT_DIR),
    )
    if not file_path:
        print(f"  [error] download failed for {filename}")
        return {}

    print(f"  [convert] PDF → Markdown")
    try:
        import fitz as _fitz
        with _fitz.open(file_path) as _doc:
            if _doc.needs_pass:
                return _handle_protected_file(filename, sender, subject)
        converted_path, md_content = pdf_to_markdown(file_path, str(AGENT_DIR))
    except Exception as e:
        print(f"  [error] pdf_to_markdown: {e}")
        converted_path, md_content = "", ""

    print(f"  [images] extracting")
    try:
        image_paths = extract_pdf_images(file_path, str(AGENT_DIR))
    except Exception as e:
        print(f"  [error] extract_pdf_images: {e}")
        image_paths = []

    page_count = 0
    try:
        import fitz
        with fitz.open(file_path) as doc:
            page_count = len(doc)
    except Exception:
        pass

    print(f"  [summarize] via {SUMMARY_MODEL}")
    summary = summarize_text(md_content or "(no text extracted)")

    row = _send_and_track(
        file_path=file_path,
        converted_path=converted_path,
        file_type="pdf",
        sender=sender,
        subject=subject,
        filename=filename,
        size_kb=size_kb,
        extra_meta=f"{page_count} pages, {len(image_paths)} images",
        summary=summary,
    )
    row["page_count"] = page_count
    row["image_count"] = len(image_paths)
    return row


def process_excel(message_id: str, sender: str, subject: str, attachment: dict) -> dict:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    filename = attachment["filename"]
    size_kb = attachment["size"] // 1024

    print(f"  [download] {filename} from {sender}")
    file_path = download_attachment(
        message_id=message_id,
        attachment_id=attachment["attachment_id"],
        filename=filename,
        dest_dir=str(AGENT_DIR),
    )
    if not file_path:
        print(f"  [error] download failed for {filename}")
        return {}

    print(f"  [convert] Excel → Markdown")
    try:
        converted_path, md_content = excel_to_markdown(file_path, str(AGENT_DIR))
        meta = excel_metadata(file_path)
        sheet_summary = ", ".join(f"{s} ({r} rows)" for s, r in meta.items())
    except PasswordProtectedError:
        return _handle_protected_file(filename, sender, subject)
    except Exception as e:
        print(f"  [error] excel_to_markdown: {e}")
        converted_path, md_content, sheet_summary = "", "", ""

    print(f"  [summarize] via {SUMMARY_MODEL}")
    summary = summarize_text(md_content or "(no content extracted)")

    return _send_and_track(
        file_path=file_path,
        converted_path=converted_path,
        file_type="excel",
        sender=sender,
        subject=subject,
        filename=filename,
        size_kb=size_kb,
        extra_meta=sheet_summary or "Excel workbook",
        summary=summary,
    )


def process_docx(message_id: str, sender: str, subject: str, attachment: dict) -> dict:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    filename = attachment["filename"]
    size_kb = attachment["size"] // 1024

    print(f"  [download] {filename} from {sender}")
    file_path = download_attachment(
        message_id=message_id,
        attachment_id=attachment["attachment_id"],
        filename=filename,
        dest_dir=str(AGENT_DIR),
    )
    if not file_path:
        print(f"  [error] download failed for {filename}")
        return {}

    print(f"  [convert] Word → Markdown")
    try:
        converted_path, md_content = docx_to_markdown(file_path, str(AGENT_DIR))
    except PasswordProtectedError:
        return _handle_protected_file(filename, sender, subject)
    except Exception as e:
        print(f"  [error] docx_to_markdown: {e}")
        converted_path, md_content = "", ""

    print(f"  [summarize] via {SUMMARY_MODEL}")
    summary = summarize_text(md_content or "(no text extracted)")

    return _send_and_track(
        file_path=file_path,
        converted_path=converted_path,
        file_type="word",
        sender=sender,
        subject=subject,
        filename=filename,
        size_kb=size_kb,
        extra_meta="Word document",
        summary=summary,
    )


def _handle_protected_file(filename: str, sender: str, subject: str) -> dict:
    """
    Called when a file is password-protected.
    Sends a Telegram alert, pushes an in-app notification, and returns {}.
    """
    msg = (
        f"Password-protected file — cannot process\n"
        f"File: {filename}\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Ask the sender to share an unprotected version."
    )
    try:
        if not _SUPPRESS_TELEGRAM:
            send_telegram(msg)
    except Exception as e:
        print(f"  [telegram error] {e}")
    notify(
        message=f"{filename} from {sender} is password-protected and could not be processed.",
        level="warning",
        source="inbox_monitor",
        action_label="",
        action_url="",
    )
    print(f"  [protected] {filename} — skipped, user notified")
    return {}


def _dispatch_attachment(message_id: str, sender: str, subject: str, att: dict) -> dict:
    """Route an attachment to the correct processor by file extension."""
    ext = Path(att["filename"]).suffix.lower()
    if ext == ".pdf":
        return process_pdf(message_id, sender, subject, att)
    elif ext in (".xlsx", ".xls"):
        return process_excel(message_id, sender, subject, att)
    elif ext in (".docx", ".doc"):
        return process_docx(message_id, sender, subject, att)
    else:
        print(f"  [skip] unsupported extension: {att['filename']}")
        return {}


# ── Tick ────────────────────────────────────────────────────────────────────────

def run_tick(seen_ids: set, job_id: str | None = None, senders: list | None = None, suppress_telegram: bool = False) -> set:
    """
    One check cycle: find new emails with attachments, process each, return updated seen_ids.
    senders: override the module-level WATCHED_SENDERS for this tick only.
    """
    global _SUPPRESS_TELEGRAM
    _SUPPRESS_TELEGRAM = suppress_telegram
    effective_senders = senders if senders is not None else WATCHED_SENDERS
    print(f"\n[tick] {datetime.now().isoformat()} — checking inbox")

    emails = list_emails_with_attachments(
        senders=effective_senders,
        seen_ids=seen_ids,
    )

    if not emails:
        print("[tick] no new attachments")
        return seen_ids

    if job_id:
        job_queue.checkpoint(job_id, "emails_fetched")

    for email in emails:
        mid = email["message_id"]
        
        seen_ids.add(mid)
        job_queue.save_seen_id(mid)  # persist immediately (crash-safe)
        for att in email["attachments"]:
            _dispatch_attachment(
                message_id=mid,
                sender=email["sender"],
                subject=email["subject"],
                att=att,
            )
        if job_id:
            job_queue.checkpoint(job_id, f"processed_{mid[:8]}")

    return seen_ids


def run_as_job(params: dict, job_id: str) -> None:
    """Entry point called by the worker. Runs one tick with checkpoint tracking."""
    job_queue.init()
    seen_ids = load_seen_ids()
    job_queue.checkpoint(job_id, "started")
    run_tick(seen_ids, job_id=job_id)


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one tick and exit")
    args = parser.parse_args()

    seen_ids = load_seen_ids()
    print(f"[job] Inbox Monitor started. Loaded {len(seen_ids)} seen IDs.")
    print(f"[job] Watching {len(WATCHED_SENDERS)} senders: {', '.join(WATCHED_SENDERS)}")
    print(f"[job] Agent dir: {AGENT_DIR}")

    if args.once:
        run_tick(seen_ids)
        return

    for tick in range(1, TOTAL_TICKS + 1):
        print(f"\n{'='*50}")
        print(f"[job] Tick {tick}/{TOTAL_TICKS}")
        seen_ids = run_tick(seen_ids)

        if tick < TOTAL_TICKS:
            print(f"[job] Sleeping {TICK_INTERVAL_SEC // 60} min until next tick...")
            time.sleep(TICK_INTERVAL_SEC)

    print("\n[job] 1-hour run complete.")
