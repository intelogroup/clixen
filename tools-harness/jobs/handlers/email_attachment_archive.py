"""
Handler: email.attachment_archive
Hourly: downloads every inbox attachment (any sender, any file type) into
~/Documents/email-attachments/<Month>/w<N>/, foldered by the email's received date.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from store import workflow_store

_log = logging.getLogger(__name__)

ARCHIVE_DIR = Path.home() / "Documents" / "email-attachments"


def _month_week_dir(internal_date_ms: str) -> Path:
    dt = datetime.fromtimestamp(int(internal_date_ms) / 1000) if internal_date_ms else datetime.now()
    week = min((dt.day - 1) // 7 + 1, 4)  # ponytail: simple day-of-month bucketing, capped at w4
    return ARCHIVE_DIR / dt.strftime("%B") / f"w{week}"


def handle(instance: dict) -> dict:
    config = dict(instance.get("config") or {})
    seen_ids_list = list(config.get("seen_ids", []))
    seen_ids = set(seen_ids_list)

    from tools.gmail import list_all_attachments, download_attachment

    try:
        emails = list_all_attachments(seen_ids)
    except Exception as exc:
        _log.error("email.attachment_archive: list_all_attachments error: %s", exc)
        return {"success": False, "items_processed": 0, "error": str(exc)}

    saved = 0
    for email in emails:
        mid = email["message_id"]
        dest_dir = _month_week_dir(email.get("internal_date", ""))
        for att in email["attachments"]:
            path = download_attachment(
                message_id=mid,
                attachment_id=att["attachment_id"],
                filename=att["filename"],
                dest_dir=str(dest_dir),
            )
            if path:
                saved += 1
        seen_ids_list.append(mid)

    seen_ids_list = seen_ids_list[-2000:]
    workflow_store.update_workflow_instance(
        instance["id"],
        config={**config, "seen_ids": seen_ids_list},
    )

    if saved:
        try:
            from tools.semantic_files import index_directory
            index_directory(str(ARCHIVE_DIR))
        except Exception as exc:
            _log.warning("email.attachment_archive: indexing failed: %s", exc)

    return {"success": True, "items_processed": saved}
