"""
Handler: email.watch_sender
Polls Gmail for new emails from configured senders, forwards raw content to Telegram.
Reuses core logic from scripts.email_watch.
"""
from __future__ import annotations

import logging
import os

from scripts.email_watch import (
    _send_telegram,
    _strip_asterisks,
    _extract_ids,
)
from tools.gmail import list_emails, read_email
from store import workflow_store

_log = logging.getLogger(__name__)


def handle(instance: dict) -> dict:
    """
    instance["config"] keys:
        senders:   list[str]  — email addresses to watch
        model:     str        — LLM model (default: gemma4)
        seen_ids:  list[str]  — persisted seen message IDs
    """
    config = dict(instance.get("config") or {})
    seen_ids_list = list(config.get("seen_ids", []))
    seen_ids = set(seen_ids_list)  # fast lookup only

    senders = config.get("senders") or []
    if not senders:
        env_raw = os.environ.get("WATCHED_SENDERS", os.environ.get("EMAIL_WATCH_SENDERS", "")).strip()
        senders = [s.strip() for s in env_raw.split(",") if s.strip()]
    if not senders:
        _log.warning("email.watch_sender: no senders configured")
        return {"success": False, "items_processed": 0, "error": "no senders configured"}

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "").strip()
    if not token or not chat_id:
        _log.error("email.watch_sender: missing TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_CHAT_ID")
        return {"success": False, "items_processed": 0, "error": "missing telegram credentials"}

    items_processed = 0
    failures = 0

    for sender in senders:
        query = f'from:"{sender}" newer_than:7d'
        listing = list_emails(query=query, max_results=10)
        if listing.startswith("[gmail error]"):
            _log.error("email.watch_sender: gmail error for %s: %s", sender, listing)
            failures += 1
            continue
        if "No emails found." in listing:
            continue

        ids = _extract_ids(listing)
        if not ids:
            continue

        # Determine which IDs to process (skip already-seen)
        to_process = [mid for mid in ids if mid not in seen_ids]
        # Process oldest-first
        to_process = list(reversed(to_process))

        for mid in to_process:
            email_text = read_email(mid)
            if email_text.startswith("[gmail error]"):
                _log.error("email.watch_sender: read_email failed for %s", mid)
                failures += 1
                continue

            msg = "New email:\n" + _strip_asterisks(email_text[:1500])

            try:
                if instance.get("action_type") == "telegram":
                    _send_telegram(token, chat_id, msg)
            except Exception as exc:
                _log.error("email.watch_sender: delivery error: %s", exc)
                failures += 1

            if mid not in seen_ids:
                seen_ids_list.append(mid)
                seen_ids.add(mid)
            items_processed += 1

    # Persist seen_ids back (cap at 500 most recent — ordered)
    seen_ids_list = seen_ids_list[-500:]
    workflow_store.update_workflow_instance(
        instance["id"],
        config={**config, "seen_ids": seen_ids_list},
    )

    if items_processed == 0 and failures > 0:
        return {"success": False, "items_processed": 0, "error": f"{failures} gmail error(s)"}
    return {"success": True, "items_processed": items_processed}
