"""
Handler: study.usmle_daily
Fetches USMLE Step 1 practice questions and sends to Telegram.
Delegates to jobs.usmle_questions_job.run_briefing().
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def handle(instance: dict) -> dict:
    """Stateless — no seen_ids needed."""
    try:
        from jobs.usmle_questions_job import run_briefing
        run_briefing()
    except Exception as exc:
        _log.error("study.usmle_daily: error: %s", exc)
        return {"success": False, "items_processed": 0, "error": str(exc)}

    return {"success": True, "items_processed": 1}
