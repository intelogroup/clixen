"""Skill definitions — split out of skills_hub.py by category. See skills_hub.py for Skill/_s/SKILLS."""

from __future__ import annotations

from skills_hub import SKILLS, _s

# ── Automation Workflows ───────────────────────────────────────────────────

# ponytail: advisory-only skill, no tools — its system_prompt IS the payload.
# Reached via match_skill_for_task (returns system_prompt[:500] directly, no
# sub-agent round trip needed for a pure checklist) rather than run_skill
# (which would spin a full LLM loop for text that's already the final
# answer). Moved out of the always-loaded ORCHESTRATOR_SYSTEM_PROMPT
# (2026-07-17) to stop that prompt growing on every future incident — every
# item here traces to a real live bug in the BCH Assignment Auto-Responder,
# not a hypothetical.
SKILLS.append(_s(
    "Automation Edge Case Checklist",
    "3 unstated-default checks for any automation with side effects, beyond DSL-branching gaps.",
    "Workflows", [],
    "Before creating/triggering an automation with side effects (send/reply/"
    "calendar/webhook), confirm 3 defaults if unstated, or name the silent "
    "default and ask instead of building: "
    "(1) RETRY-ON-FAILURE — retry next run or skip? An unasked 'retry' can "
    "re-fire a real send every poll (happened live: duplicate SMS to a real "
    "contact for over an hour). "
    "(2) CALENDAR CONFLICT WINDOW — check the FULL duration of the thing "
    "being checked, not just its start instant, or a conflict starting after "
    "the start is missed (happened live: a 10:40AM-1:40PM assignment "
    "auto-accepted over a 12:50PM conflict). "
    "(3) TRIGGER SIGNAL vs. NOISE — when watching messages/emails for "
    "action items, what phrase/marker actually means 'act on this' vs. "
    "informational noise sharing the same date/time/location fields "
    "(a cancellation notice, a thank-you)? Don't assume matching fields "
    "means a real trigger.",
    ["automation edge case", "automation checklist", "unstated default",
     "workflow side effect", "retry semantics", "trigger signal vs noise"],
    max_rounds=1, icon="checklist",
    trigger_regex=r"edge case checklist|unstated default",
))

SKILLS.append(_s(
    "Morning Briefing", "Daily briefing: calendar, tasks, email overview, sent via Telegram.",
    "Workflows",
    ["list_calendar_events", "list_tasks", "list_emails", "send_telegram", "get_current_time"],
    "1. Call list_calendar_events() for today.\n"
    "2. Call list_tasks() for pending tasks.\n"
    "3. Call list_emails() for recent unread.\n"
    "4. Compile a concise briefing: top 3-5 items across all sources.\n"
    "5. Call send_telegram() with the briefing.",
    ["morning briefing", "daily brief", "start my day", "what's up today", "daily summary", "daily briefing"],
    max_rounds=8, icon="sunrise",
))

SKILLS.append(_s(
    "Inbox Monitor", "Run the full inbox pipeline: check email, process PDFs, notify via Telegram.",
    "Workflows",
    ["run_inbox_monitor", "list_emails", "read_email", "send_telegram", "create_task",
     "get_current_time", "refresh_google_token"],
    "Call run_inbox_monitor() to execute the full inbox pipeline. "
    "After it returns, report the result to the user. "
    "If there are new actionable items, also call create_task() for each.",
    ["inbox monitor", "process inbox", "check all emails", "run inbox pipeline"],
    max_rounds=10, icon="inbox",
))

SKILLS.append(_s(
    "Founder Brief", "Inbox + tasks + web search for a founder-style daily brief.",
    "Workflows",
    ["list_emails", "list_tasks", "web_search", "send_telegram",
     "create_task", "get_current_time"],
    "1. Call list_emails() for unread messages.\n"
    "2. Call list_tasks() for pending items.\n"
    "3. Call web_search() for relevant market/competitor updates.\n"
    "4. Compile a brief: key signals, action items, market update.\n"
    "5. Call send_telegram() with the brief.\n"
    "6. If there are concrete follow-ups, call create_task() for each.",
    ["founder brief", "founder pipeline", "startup dashboard", "what should I focus on"],
    max_rounds=8, icon="briefcase",
))

SKILLS.append(_s(
    "Watch Topic", "Search for updates on a specific topic and alert via Telegram.",
    "Workflows",
    ["web_search", "send_telegram", "get_current_time"],
    "1. Extract the topic from the user's message.\n"
    "2. Call web_search() for the latest updates on that topic.\n"
    "3. Filter to only meaningful/important updates.\n"
    "4. Call send_telegram() with a concise alert.\n"
    "5. Report back to the user what was found and sent.",
    ["watch", "monitor", "track", "alert me about", "notify me when", "watch for", "keep an eye on"],
    max_rounds=5, icon="radar", trigger_regex=r"\b(watch|monitor|track|alert me|keep an eye)\b",
))

SKILLS.append(_s(
    "Knowledge to Action", "Read docs/notes/research, extract action items, create tasks.",
    "Workflows",
    ["read_file", "read_document", "list_tasks", "create_task", "send_telegram", "get_current_time"],
    "1. Read the material the user referenced.\n"
    "2. Extract key findings and action items.\n"
    "3. Call create_task() for each concrete action item.\n"
    "4. Call send_telegram() with a brief summary.\n"
    "5. Report how many tasks were created.",
    ["knowledge to action", "extract tasks", "turn notes into tasks", "action items"],
    max_rounds=8, icon="spark",
))

SKILLS.append(_s(
    "Cleanup Admin", "Review tasks/emails for loose obligations, create prioritized checklist.",
    "Workflows",
    ["list_tasks", "list_emails", "create_task", "send_telegram", "get_current_time"],
    "1. Call list_tasks() and list_emails() to survey the landscape.\n"
    "2. Identify loose obligations: unresponded emails, stale tasks, recurring items.\n"
    "3. Create a prioritized checklist with new create_task() calls as needed.\n"
    "4. Call send_telegram() with the checklist.",
    ["cleanup", "admin cleanup", "organize tasks", "prioritize", "what should I do", "clear backlog",
     "clean up tasks", "clean up my", "triage", "sort out"],
    max_rounds=7, icon="clipboard",
))

SKILLS.append(_s(
    "Travel Prep", "Prepare a travel-mode checklist with reminders, timing, and key info.",
    "Workflows",
    ["list_calendar_events", "web_search", "set_reminder", "send_telegram", "get_current_time"],
    "1. Check the user's calendar for travel dates.\n"
    "2. Call web_search() for weather, flight status, or destination info if needed.\n"
    "3. Set a reminder for critical timing.\n"
    "4. Compile a checklist: timing, items, reminders.\n"
    "5. Call send_telegram() with the full checklist.",
    ["travel prep", "travel checklist", "trip planning", "packing list", "travel mode"],
    max_rounds=8, icon="plane", trigger_regex=r"\b(travel|trip|vacation|flight|packing|itinerary)\b",
))

SKILLS.append(_s(
    "OCR Image", "Extract text from an image using OCR.",
    "Documents",
    ["ocr_image"],
    "Call ocr_image() to extract text from the image. "
    "Return the extracted text clearly formatted.",
    ["ocr", "extract text from image", "read image", "text from screenshot"],
    max_rounds=2, icon="eye",
))

# ── Auth Maintenance ───────────────────────────────────────────────────────

SKILLS.append(_s(
    "Refresh Google Auth", "Refresh the Google OAuth2 access token without browser interaction.",
    "Admin",
    ["refresh_google_token"],
    "Call refresh_google_token() immediately. "
    "If it returns 'Token refreshed successfully', report success and the new expiry time. "
    "If it returns an error containing 'No refresh_token' or 'Re-auth required', "
    "tell the user: 'Full re-auth needed — run: python tools/google_auth.py --auth in the harness directory.' "
    "Do not attempt any other Google tools until refresh succeeds.",
    ["refresh google token", "google auth expired", "auth error", "token expired",
     "google 401", "google 403", "re-authenticate google", "fix google auth",
     "google auth", "oauth expired"],
    max_rounds=2, icon="key",
    trigger_regex=r"\b(refresh\s+google|google\s+auth|token\s+expir|auth\s+error|401|403\s+auth|re.?auth(enticate)?\s+google)\b",
))

# ── Automation Management ──────────────────────────────────────────────────

SKILLS.append(_s(
    "List Automations", "List all persistent background automations.",
    "Admin",
    ["list_automations"],
    "Call list_automations() and show all active/paused background automations "
    "with their status, schedule, and last run time.",
    ["list automations", "show automations", "what automations are running"],
    max_rounds=2, icon="settings",
))

SKILLS.append(_s(
    "Create Automation", "Create a persistent background automation.",
    "Admin",
    ["create_automation", "get_current_time"],
    "Extract the trigger type (email/schedule/webhook), action type, and schedule from the user's request. "
    "Call create_automation() with those details. Confirm the automation was created and its ID.",
    ["create automation", "set up automation", "new automation", "schedule recurring"],
    max_rounds=4, icon="settings",
))


