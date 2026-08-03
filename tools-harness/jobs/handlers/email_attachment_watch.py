"""
Handler: email.attachment_watch
Lightweight sibling to inbox.monitor_attachments — that handler runs a full
PDF/Excel/Word -> markdown -> LLM-summary -> tracker -> Google Tasks pipeline
for two specific hardcoded addresses (jobs/inbox_monitor_job.py). This one is
for "just watch a sender, save whatever they attach, tell me" requests where
that heavier processing isn't wanted: any attachment type, a configurable
destination folder, one Telegram line per new message.
"""
from __future__ import annotations

import logging
from pathlib import Path

from store import workflow_store
from tools.gmail import list_all_attachments, download_attachment
from tools.telegram_send import send_telegram

_log = logging.getLogger(__name__)

DEFAULT_DEST_DIR = str(Path.home() / "Documents" / "attachments")


def handle(instance: dict) -> dict:
    """
    instance["config"] keys:
        senders:   list[str]  — substrings matched against the email's From header
                                 (e.g. ["james"] matches "James Smith <james@x.com>")
        dest_dir:  str        — folder to save attachments into (default: ~/Documents/attachments)
        seen_ids:  list[str]  — persisted seen message IDs
    """
    config = dict(instance.get("config") or {})
    seen_ids_list = list(config.get("seen_ids", []))
    seen_ids = set(seen_ids_list)

    senders = [s.strip().lower() for s in (config.get("senders") or []) if s.strip()]
    dest_dir = str(Path(config.get("dest_dir") or DEFAULT_DEST_DIR).expanduser())

    try:
        messages = list_all_attachments(seen_ids)
    except Exception as exc:
        _log.error("email.attachment_watch: list_all_attachments error: %s", exc)
        return {"success": False, "error": str(exc)}

    if senders:
        messages = [
            m for m in messages
            if any(s in (m.get("sender") or "").lower() for s in senders)
        ]

    Path(dest_dir).mkdir(parents=True, exist_ok=True)

    new_ids = []
    notified = 0
    for msg in messages:
        mid = msg["message_id"]
        new_ids.append(mid)
        sender = msg.get("sender", "")
        subject = msg.get("subject", "")
        saved = []
        for att in msg.get("attachments", []):
            path = download_attachment(
                message_id=mid, attachment_id=att["attachment_id"],
                filename=att["filename"], dest_dir=dest_dir,
            )
            if path:
                saved.append(path)
        if instance.get("action_type") == "telegram":
            if saved:
                send_telegram(f"📎 New email from {sender}: {subject}\nSaved {len(saved)} attachment(s) to {dest_dir}")
            else:
                send_telegram(f"📬 New email from {sender}: {subject} (no attachment could be saved)")
        notified += 1

    seen_ids_list = (seen_ids_list + new_ids)[-500:]
    workflow_store.update_workflow_instance(
        instance["id"],
        config={**config, "seen_ids": seen_ids_list},
    )

    return {"success": True, "new_messages": len(new_ids), "notified": notified}
