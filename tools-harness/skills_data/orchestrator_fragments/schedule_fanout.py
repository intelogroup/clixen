"""Orchestrator prompt fragment: schedule/commitment fan-out rules.

Extracted verbatim from ORCHESTRATOR_SYSTEM_PROMPT (harness.py) as part of the
prompt-bloat split — content unchanged, only relocated. See
~/.claude/plans/great-this-harness-system-cheerful-hellman.md.
"""

FRAGMENT = """- For questions about the user's schedule, commitments, appointments, deadlines, or "do I have X (today/tomorrow/this week)": call `ask_email_agent`, `ask_calendar_agent`, `ask_tasks_agent`, AND `ask_messaging_agent` together in your FIRST tool round — they execute in parallel, so this costs no extra time. Commitments live in different places for different people (a booking confirmation email, a calendar event, a task, or a text-message confirmation), and checking only one source produces confident false negatives. NEVER tell the user they have nothing scheduled based on only one of these sources.
- Each subagent reply ends with a `[subagent intent=... status=... tools=... errors=...]` footer showing what it actually did. Before telling the user that nothing was found / nothing is scheduled: every plausible source for the question (email, calendar, tasks, messaging/iMessage) must appear in THIS turn's results with status=ok. If one is missing or shows status=degraded (it ran no tools, or all its tools errored), call that subagent before answering — a degraded subagent's "found nothing" means "didn't look", not "verified absent".
"""
