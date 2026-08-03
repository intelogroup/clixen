"""
Handler: email.daily_summary
Fetches last 24h emails, runs 2-stage LLM summary, sends to Telegram.
Delegates entirely to scripts.daily_email_summary.main().
"""
from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)


def handle(instance: dict) -> dict:
    """
    instance["config"] keys (all optional):
        model: str  — overrides EMAIL_SUMMARY_STAGE1_MODEL / STAGE2_MODEL env vars
    Stateless — always fetches last 24h, no seen_ids needed.
    """
    config = dict(instance.get("config") or {})
    model = config.get("model")

    try:
        from scripts.daily_email_summary import main as _run
        rc = _run(model=model)
        success = rc == 0
    except Exception as exc:
        _log.error("email.daily_summary: error: %s", exc)
        return {"success": False, "items_processed": 0, "error": str(exc)}

    return {"success": success, "items_processed": 1 if success else 0}
