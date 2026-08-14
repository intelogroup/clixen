"""
Handler: study.usmle_daily
Runs the multi-step agentic USMLE pipeline:
  research PubMed → generate source-backed questions → verify answers
  → semantic dedup → deliver via Telegram.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def handle(instance: dict) -> dict:
    try:
        from workflows.usmle_daily import execute
        result = execute()
        steps = result.get("steps", [])
        failures = [s for s in steps if not s.get("result", {}).get("ok")]
        if failures:
            _log.error("usmle pipeline: %d/%d steps failed", len(failures), len(steps))
            return {"success": False, "items_processed": 0,
                    "error": f"{len(failures)} step failures"}
        _log.info("usmle pipeline: %d steps OK", len(steps))
        return {"success": True, "items_processed": 1}
    except Exception as exc:
        _log.error("study.usmle_daily: error: %s", exc)
        return {"success": False, "items_processed": 0, "error": str(exc)}
