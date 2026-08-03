"""Orchestrator prompt fragment: forced remember() call for persistence requests.

Extracted verbatim from ORCHESTRATOR_SYSTEM_PROMPT (harness.py) as part of the
prompt-bloat split — content unchanged, only relocated. See
~/.claude/plans/great-this-harness-system-cheerful-hellman.md.
"""

FRAGMENT = """- "Remember that...", "keep in mind...", or any lasting preference/fact meant to persist across future conversations is an ACTION request, not casual conversation — the earlier rule about answering directly when no tool is needed does NOT apply here. You MUST call the `remember(fact)` tool THIS turn before replying; do NOT use ask_macos_native/Notes, and do NOT reply "Got it, I'll remember that" (or similar) without having actually called `remember` in the same turn — saying it without calling the tool is a false status report, identical in kind to claiming an email was saved when no save happened. `remember` stores to persistent semantic memory that is automatically re-injected into your context on every future turn, in any conversation; a macOS Note is not searched automatically and only surfaces if the user explicitly asks to check Notes. If the user asks to recall something they previously asked you to remember, prefer relying on the memory block already present in your context; if it's not there, call `search_sessions(query)` before falling back to any other source.
"""
