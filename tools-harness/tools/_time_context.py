"""Shared local date/time/timezone line for LLM prompts.

Any one-shot LLM call (a tool that builds its own prompt instead of going
through harness.py's orchestrator system prompt) should call now_line()
instead of hand-rolling datetime.now() — training data has a stale/frozen
notion of "today", and MM/DD-style dates without a timezone are ambiguous
across DST boundaries. Stdlib only, no dependency.
"""
from __future__ import annotations

import datetime


def now_line(now: datetime.datetime | None = None) -> str:
    """Return e.g. 'LOCAL DATE/TIME (from user's computer): Today is
    Sunday, July 12, 2026. Current time: 22:27 (timezone: EDT, UTC-04:00).'
    """
    _now = now or datetime.datetime.now().astimezone()
    tz = _now.tzname() or "local"
    tz_offset = _now.strftime("%z")
    tz_offset_fmt = f"UTC{tz_offset[:3]}:{tz_offset[3:]}" if tz_offset else ""
    return (
        f"LOCAL DATE/TIME (from user's computer): "
        f"Today is {_now.strftime('%A, %B %d, %Y')}. "
        f"Current time: {_now.strftime('%H:%M')} "
        f"(timezone: {tz}{', ' + tz_offset_fmt if tz_offset_fmt else ''})."
    )
