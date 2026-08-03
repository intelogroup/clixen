"""
Handler: inbox.monitor_attachments
Polls Gmail for PDF/Excel/Word attachments from watched senders.
Wraps jobs.inbox_monitor_job.run_tick() with config-driven sender list and seen_ids.
"""
from __future__ import annotations

import logging
import os

from store import workflow_store

_log = logging.getLogger(__name__)


def handle(instance: dict) -> dict:
    """
    instance["config"] keys:
        senders:   list[str]  — email addresses to watch for attachments
        seen_ids:  list[str]  — persisted seen message IDs
    """
    config = dict(instance.get("config") or {})
    seen_ids_list = list(config.get("seen_ids", []))
    seen_ids = set(seen_ids_list)  # fast lookup; passed to run_tick

    senders = config.get("senders") or []
    if not senders:
        env_raw = os.environ.get("WATCHED_SENDERS", "").strip()
        senders = [s.strip() for s in env_raw.split(",") if s.strip()]

    # Import here so module-level side effects (load_dotenv, sys.path) run inside handler
    import jobs.inbox_monitor_job as _job

    items_before = len(seen_ids)
    try:
        updated_ids = _job.run_tick(seen_ids, senders=senders if senders else None,
                                    suppress_telegram=instance.get("action_type") != "telegram")
    except Exception as exc:
        _log.error("inbox.monitor_attachments: run_tick error: %s", exc)
        return {"success": False, "items_processed": 0, "error": str(exc)}

    items_processed = len(updated_ids) - items_before

    # Append new IDs in deterministic (sorted) order, then cap at 500 most recent
    for new_id in sorted(updated_ids - seen_ids):
        seen_ids_list.append(new_id)
        seen_ids.add(new_id)
    seen_ids_list = seen_ids_list[-500:]

    workflow_store.update_workflow_instance(
        instance["id"],
        config={**config, "seen_ids": seen_ids_list},
    )

    return {"success": True, "items_processed": max(0, items_processed)}
