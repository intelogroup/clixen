"""
Handler: tech_brief
Sends technology news briefing via email at 7AM and 6PM daily.
Delegates to jobs.tech_brief_job.run_briefing().
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def handle(instance: dict) -> dict:
    """Stateless — no seen_ids needed."""
    try:
        from jobs.tech_brief_job import run_briefing
        run_briefing()
    except Exception as exc:
        _log.error("tech_brief: error: %s", exc)
        return {"success": False, "items_processed": 0, "error": str(exc)}

    return {"success": True, "items_processed": 1}
