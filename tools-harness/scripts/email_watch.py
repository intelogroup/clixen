#!/usr/bin/env python3
"""
Poll Gmail for new messages from a specific sender.
Summarize and send to Telegram, and create Google Tasks from action items.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

from tools.gmail import list_emails, read_email
from tools.gtasks import create_task
from clients.ollama_client import chat as ollama_chat
from clients.cloud_client import chat as cloud_chat
from pydantic import BaseModel, ValidationError


def _load_env() -> None:
    if load_dotenv is None:
        return
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def _strip_asterisks(text: str) -> str:
    return text.replace("*", "")


def _chunk_text(text: str, size: int = 3800) -> List[str]:
    chunks: List[str] = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + size])
        i += size
    return chunks


def _send_telegram(token: str, chat_id: str, text: str, sleep_s: float = 0.3) -> None:
    import urllib.parse
    import urllib.request
    import time

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    clean = _strip_asterisks(text)
    for chunk in _chunk_text(clean):
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": chunk}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        time.sleep(sleep_s)


def _extract_ids(listing: str) -> List[str]:
    ids: List[str] = []
    for line in listing.splitlines():
        if line.startswith("ID: "):
            ids.append(line.replace("ID: ", "", 1).strip())
    return ids


def _state_path() -> Path:
    base = Path(os.path.expanduser("~/.cache/clixen"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "email_watch.json"


def _load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _state_path().write_text(json.dumps(state, indent=2))


class EmailSummary(BaseModel):
    summary: str = ""
    action_items: List[str] = []


def _extract_json_block(text: str) -> str | None:
    # Strip common code fences
    cleaned = text.strip().strip("`")
    # Try to find the first JSON object
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return cleaned[start : end + 1]


def _summarize(email_text: str, model: str) -> dict:
    prompt = (
        "Summarize this email and extract action items. "
        "Return ONLY valid JSON with keys: summary (string), action_items (list of strings). "
        "No markdown, no extra text, no code fences.\n\nEmail:\n" + email_text
    )
    from clients.cloud_client import is_cloud_model
    if is_cloud_model(model):
        # Cloud-first (2026-07-31): cloud-prefixed models go straight to the
        # cloud path — skipping the ollama call avoids a guaranteed 404 (local
        # gemma4:12b-mlx is not reliably loaded in this env) and the fallback
        # hop that would follow.
        raw = cloud_chat(user_message=prompt, max_rounds=1, bypass_budget=True)
    else:
        try:
            raw = ollama_chat(prompt, model=model)
        except Exception:
            # Local model unreachable/unloaded (e.g. gemma4:12b-mlx 404) — fall back
            # to cloud rather than crash-looping the workflow (see harness.py's
            # _check_claim_against_trace for the same pattern).
            raw = cloud_chat(user_message=prompt, max_rounds=1, bypass_budget=True)
    try:
        candidate = raw.strip()
        data = json.loads(candidate)
        validated = EmailSummary.model_validate(data)
        return {"summary": validated.summary, "action_items": validated.action_items}
    except (ValidationError, json.JSONDecodeError, ValueError):
        try:
            block = _extract_json_block(raw)
            if block:
                data = json.loads(block)
                validated = EmailSummary.model_validate(data)
                return {"summary": validated.summary, "action_items": validated.action_items}
        except Exception:
            pass
        # Fallback: treat whole response as summary
        return {"summary": raw.strip(), "action_items": []}


def _self_test_emails() -> List[str]:
    return [
        "From: watched@example.com\nSubject: Vendor update\nDate: Today\n\nPlease review the attached vendor contract and confirm by Friday.",
        "From: watched@example.com\nSubject: Meeting follow-up\nDate: Today\n\nAction items: 1) Send the deck to the team. 2) Schedule a 30-min check-in next week.",
    ]


def _run_once() -> int:
    senders_raw = os.environ.get("EMAIL_WATCH_SENDERS", "").strip()
    if senders_raw:
        senders = [s.strip() for s in senders_raw.split(",") if s.strip()]
    else:
        single = os.environ.get("EMAIL_WATCH_SENDER", "").strip()
        senders = [single] if single else []
    if not senders:
        print("Missing EMAIL_WATCH_SENDERS (or EMAIL_WATCH_SENDER) in env", file=sys.stderr)
        return 2

    rc = 0
    for sender in senders:
        rc = max(rc, _run_for_sender(sender))
    return rc


def _run_for_sender(sender: str) -> int:

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_CHAT_ID in .env", file=sys.stderr)
        return 2

    from clients.cloud_client import DEFAULT_CLOUD_MODEL
    model = os.environ.get("EMAIL_WATCH_MODEL", DEFAULT_CLOUD_MODEL)
    query = os.environ.get(
        "EMAIL_WATCH_QUERY", f'from:"{sender}" newer_than:7d'
    )
    bootstrap = os.environ.get("EMAIL_WATCH_BOOTSTRAP", "latest")  # skip|all|latest
    dry_run = os.environ.get("EMAIL_WATCH_DRY_RUN", "0") == "1"
    self_test = os.environ.get("EMAIL_WATCH_SELF_TEST", "0") == "1"
    force_latest = os.environ.get("EMAIL_WATCH_FORCE_LATEST", "0") == "1"
    stress_n = int(os.environ.get("EMAIL_WATCH_STRESS_N", "1"))

    if self_test:
        emails = _self_test_emails()
        for _ in range(max(1, stress_n)):
            for email_text in emails:
                data = _summarize(email_text, model=model)
                summary = data.get("summary", "").strip()
                actions = [a.strip() for a in data.get("action_items", []) if a.strip()]

                msg = "New email summary:\n" + summary
                if actions:
                    msg += "\n\nAction items:\n" + "\n".join(f"- {a}" for a in actions)

                if dry_run:
                    print(_strip_asterisks(msg))
                else:
                    _send_telegram(token, chat_id, msg)
                    for action in actions:
                        create_task(title=action)
        return 0

    listing = list_emails(query=query, max_results=10)
    if listing.startswith("[gmail error]"):
        print(listing, file=sys.stderr)
        return 1
    if "No emails found." in listing:
        return 0

    ids = _extract_ids(listing)
    if not ids:
        return 0

    state = _load_state()
    key = f"sender:{sender}"
    seen_key = f"seen:{sender}"
    last_seen = state.get(key)
    # Seen-id set, not just a single cursor — if a sender's last-processed id
    # scrolls past list_emails' max_results=10 window (sender sent 10+ in the
    # 7d query range), "last_seen not in ids" used to fall back to
    # reprocessing the WHOLE listing, re-summarizing already-sent emails with
    # a fresh (non-deterministic) LLM call each time — looked like a dup with
    # reworded text. Mirrors jobs/handlers/email_watch_sender.py's approach.
    seen_ids = set(state.get(seen_key, []))

    if force_latest:
        to_process = [ids[0]]
    elif not last_seen:
        if bootstrap == "all":
            to_process = list(reversed(ids))
        elif bootstrap == "latest":
            to_process = [ids[0]]
        else:
            state[key] = ids[0]
            state[seen_key] = ids[:1]
            _save_state(state)
            return 0
    else:
        to_process = list(reversed([mid for mid in ids if mid not in seen_ids]))

    if not to_process:
        return 0

    for mid in to_process:
        email_text = read_email(mid)
        if email_text.startswith("[gmail error]"):
            print(email_text, file=sys.stderr)
            continue

        data = _summarize(email_text, model=model)
        summary = data.get("summary", "").strip()
        actions = [a.strip() for a in data.get("action_items", []) if a.strip()]

        msg = "New email summary:\n" + summary
        if actions:
            msg += "\n\nAction items:\n" + "\n".join(f"- {a}" for a in actions)

        if dry_run:
            print(_strip_asterisks(msg))
        else:
            _send_telegram(token, chat_id, msg)
            for action in actions:
                create_task(title=action)

        seen_ids.add(mid)
        state[key] = mid
        state[seen_key] = list(seen_ids)[-500:]
        _save_state(state)

    return 0


def main() -> int:
    _load_env()

    loop_enabled = os.environ.get("EMAIL_WATCH_LOOP", "0") == "1"
    poll_seconds = max(5, int(os.environ.get("EMAIL_WATCH_POLL_SECONDS", "60")))

    if not loop_enabled:
        return _run_once()

    while True:
        try:
            _run_once()
        except Exception as exc:
            print(f"[email_watch loop error] {exc}", file=sys.stderr)
        time.sleep(poll_seconds)
