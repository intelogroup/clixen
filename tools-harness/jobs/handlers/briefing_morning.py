"""
Handler: briefing.morning
Sends morning briefing (calendar + emails + tasks) to Telegram.
Delegates to jobs.morning_briefing_job.run_briefing().
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def handle(instance: dict) -> dict:
    """Stateless — no seen_ids needed."""
    try:
        from jobs.morning_briefing_job import run_briefing
        run_briefing()
    except Exception as exc:
        _log.error("briefing.morning: error: %s", exc)
        return {"success": False, "items_processed": 0, "error": str(exc)}

    return {"success": True, "items_processed": 1}
