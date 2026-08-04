"""Orchestrator prompt fragment: promote_task_to_skill trigger rule.

Narrow, explicit-trigger-phrase-gated instruction — same shape as
remember_action.py. Moved out of the static ORCHESTRATOR_SYSTEM_PROMPT block
to keep it from permanently bloating every turn's prompt for a rarely-used
capability. See CLAUDE.md's context-engineering guidance / Anthropic's
"effective context engineering for AI agents".
"""

FRAGMENT = """- SKILL PROMOTION: If the user explicitly asks to save/remember the task you just completed as a skill (e.g. "save this as a skill", "remember how you did that"), call `promote_task_to_skill()` (no args — it reads the last completed task in this chat). Never call it proactively/unprompted.
"""
