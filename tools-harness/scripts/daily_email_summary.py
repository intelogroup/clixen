#!/usr/bin/env python3
"""
Daily Gmail summary -> Telegram.

Fetches emails from the last 24 hours, summarizes them in two stages
to keep context bounded, and sends the summary to a Telegram chat.
"""

from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
from typing import List


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

from tools.gmail import list_emails, read_email
from clients.ollama_client import chat as ollama_chat


def _chat_with_fallback(prompt: str, model: str) -> str:
    from clients.cloud_client import is_cloud_model
    if is_cloud_model(model):
        # Cloud-first (2026-07-31): cloud-prefixed models go straight to cloud —
        # skip the guaranteed-fail ollama hop when the local model isn't loaded.
        from clients.cloud_client import chat as cloud_chat
        return cloud_chat(user_message=prompt, max_rounds=1, bypass_budget=True)
    try:
        return ollama_chat(prompt, model=model)
    except Exception:
        # Local model unreachable/unloaded (e.g. gemma4:12b-mlx 404) — fall back
        # to cloud rather than crash-looping the workflow (see harness.py's
        # _check_claim_against_trace / scripts/email_watch.py._summarize).
        from clients.cloud_client import chat as cloud_chat

        return cloud_chat(user_message=prompt, max_rounds=1, bypass_budget=True)


log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _load_env() -> None:
    if load_dotenv is None:
        return
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def _extract_ids(listing: str) -> List[str]:
    ids: List[str] = []
    for line in listing.splitlines():
        line = line.strip()
        if line.startswith("ID: "):
            ids.append(line.replace("ID: ", "", 1).strip())
    return ids


def _chunk_text(text: str, size: int = 3800) -> List[str]:
    chunks: List[str] = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + size])
        i += size
    return chunks


def _strip_asterisks(text: str) -> str:
    return text.replace("*", "")


def _send_telegram(token: str, chat_id: str, text: str, sleep_s: float = 0.3) -> None:
    import urllib.parse
    import urllib.request
    import time

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in _chunk_text(_strip_asterisks(text)):
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": chunk}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        time.sleep(sleep_s)


def _stage1_prompt(email_text: str) -> str:
    return (
        "Summarize this single email. Return 2-5 bullets with:\n"
        "- Sender\n"
        "- Subject\n"
        "- Key points\n"
        "- Any action needed (if none, say 'None')\n\n"
        f"Email:\n{email_text}"
    )


def _stage2_prompt(summaries: List[str]) -> str:
    return (
        "Summarize the following per-email summaries from the last 24 hours.\n\n"
        "Return three sections:\n"
        "1. Overall summary (3-6 bullets)\n"
        "2. Action items (with sender)\n"
        "3. Important dates/times mentioned\n\n"
        "Summaries:\n" + "\n\n---\n\n".join(summaries)
    )


def _self_test_data() -> List[str]:
    return [
        "From: alice@example.com\nSubject: Project update\nDate: Today\n\nWe shipped v1.2. Please review the changelog.",
        "From: bob@example.com\nSubject: Budget approval\nDate: Today\n\nCan you approve the Q2 budget by Friday?",
    ]


def main(model: str | None = None) -> int:
    _load_env()
    log.info("daily email summary starting")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.error("missing TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_CHAT_ID in environment")
        return 2

    query = os.environ.get("EMAIL_SUMMARY_QUERY", "newer_than:1d in:inbox")
    max_results = int(os.environ.get("EMAIL_SUMMARY_MAX", "20"))
    email_limit = int(os.environ.get("EMAIL_SUMMARY_EMAIL_LIMIT", "3"))
    per_email_char_limit = int(os.environ.get("EMAIL_SUMMARY_EMAIL_CHAR_LIMIT", "2500"))
    total_char_limit = int(os.environ.get("EMAIL_SUMMARY_TOTAL_CHAR_LIMIT", "12000"))
    dry_run = os.environ.get("EMAIL_SUMMARY_DRY_RUN", "0") == "1"
    self_test = os.environ.get("EMAIL_SUMMARY_SELF_TEST", "0") == "1"


    if self_test:
        bodies = _self_test_data()
        log.info("running self-test mode with %d synthetic emails", len(bodies))
    else:
        log.info("querying Gmail with query=%r max_results=%d", query, max_results)
        listing = list_emails(query=query, max_results=max_results)
        if "No emails found" in listing:
            log.info("no emails found for last-24h summary query")
            if not dry_run:
                _send_telegram(token, chat_id, "Email summary: no emails in the last 24 hours.")
            else:
                print("Email summary: no emails in the last 24 hours.")
            return 0

        ids = _extract_ids(listing)
        if not ids:
            log.info("gmail listing returned no parsable email ids")
            if not dry_run:
                _send_telegram(token, chat_id, "Email summary: no emails found.")
            else:
                print("Email summary: no emails found.")
            return 0

        bodies: List[str] = []
        total_chars = 0
        for mid in ids[:email_limit]:
            text = read_email(mid)
            if text.startswith("[gmail error]"):
                log.error("read_email failed for id=%s: %s", mid, text)
                continue
            if len(text) > per_email_char_limit:
                text = text[:per_email_char_limit] + "\n... [truncated]"
            total_chars += len(text)
            if total_chars > total_char_limit:
                break
            bodies.append(text)
        log.info("prepared %d email bodies for summarization", len(bodies))

    if not bodies:
        log.info("no readable emails available for summary")
        if not dry_run:
            _send_telegram(token, chat_id, "Email summary: no readable emails found.")
        else:
            print("Email summary: no readable emails found.")
        return 0

    from clients.cloud_client import DEFAULT_CLOUD_MODEL
    stage1_model = model or os.environ.get("EMAIL_SUMMARY_STAGE1_MODEL", DEFAULT_CLOUD_MODEL)
    stage2_model = model or os.environ.get("EMAIL_SUMMARY_STAGE2_MODEL", DEFAULT_CLOUD_MODEL)

    per_email_summaries: List[str] = []
    for body in bodies:
        s1 = _chat_with_fallback(_stage1_prompt(body), model=stage1_model)
        per_email_summaries.append(s1.strip())
    log.info(
        "stage1 complete for %d emails using %s; stage2 using %s",
        len(per_email_summaries),
        stage1_model,
        stage2_model,
    )

    log.info("starting stage2 summary aggregation")
    summary = _chat_with_fallback(_stage2_prompt(per_email_summaries), model=stage2_model)
    header = "Daily email summary (last 24h):\n"
    log.info("stage2 complete; summary length=%d chars", len(summary.strip()))

    if dry_run:
        print(_strip_asterisks(header + summary.strip()))
        log.info("dry run complete; summary length=%d chars", len(summary.strip()))
    else:
        log.info("sending summary to telegram chat_id=%s", chat_id)
        _send_telegram(token, chat_id, header + summary.strip())
        log.info("summary sent to telegram; summary length=%d chars", len(summary.strip()))
    return 0
