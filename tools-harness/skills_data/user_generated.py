"""User-promoted skills — built by promote_last_task_as_skill (skills_hub.py).
Persisted here so promotions survive a restart; each promotion appends one
_s(...) block below, on disk AND live to skills_hub.SKILLS in the same call."""

from __future__ import annotations

from skills_hub import SKILLS, _s  # noqa: F401
