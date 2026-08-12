"""Orchestrator prompt fragment: named-system status checks.

Split out of ORCHESTRATOR_SYSTEM_PROMPT (harness.py) as part of the
2026-08-12 prompt-bloat/coverage audit — this rule only fires on "is X
running/up/alive" style queries, not worth carrying on every single turn.
"""

FRAGMENT = """- NAMED-SYSTEM STATUS CHECKS: When asked whether a specific named bot/service/project is running (e.g. "is X bot up"), never answer by pattern-matching X onto a similarly-worded automation/workflow you already know about (a wrong keyword-overlap guess is worse than "let me check"). The user's dev machine has projects outside this repo (e.g. under ~/Developer) that this system has no automation for — use ask_run_command (find/grep/ps under the relevant directory or process list) to actually check before answering, and if the user corrects you that your answer was about the wrong thing, immediately go verify with a tool rather than telling the user to check manually themselves.
"""
