"""Skill definitions — split out of skills_hub.py by category. See skills_hub.py for Skill/_s/SKILLS."""

from __future__ import annotations

from skills_hub import SKILLS, _s

# ── Knowledge Management ────────────────────────────────────────────────────

SKILLS.append(_s(
    "Save to Google Docs", "Save article content, search results, or notes as a Google Doc.",
    "Knowledge",
    ["read_document", "create_google_doc", "append_google_doc"],
    "1. Read the source content (file, URL, or search results).\n"
    "2. Call create_google_doc(title, content) with a descriptive title.\n"
    "3. If the content is very long, use append_google_doc() for additional sections.\n"
    "4. Return the Doc URL and confirm what was saved.",
    ["save to docs", "save as doc", "create doc from", "save this to Google"],
    max_rounds=5, icon="book", trigger_regex=r"\b(save (to|this|it|the)|create doc|save as)\b",
))

SKILLS.append(_s(
    "Research Report", "Multi-source research compiled into a structured Google Doc report.",
    "Knowledge",
    ["web_search", "search_pubmed", "create_google_doc", "send_whatsapp"],
    "1. Determine the research question.\n"
    "2. Search web, PubMed, and/or arXiv for relevant sources.\n"
    "3. Compile a structured report in Google Docs: intro, findings, sources, conclusions.\n"
    "4. Include source links.\n"
    "5. Notify user via WhatsApp with the Doc link.",
    ["research report", "write report", "compile research", "deep dive", "investigate topic"],
    max_rounds=8, icon="book", trigger_regex=r"\b(research report|deep dive|compile|investigate)\b",
))

SKILLS.append(_s(
    "Drive Finder", "Find files across Google Drive by name or content.",
    "Knowledge",
    ["list_google_docs", "list_google_sheets", "read_google_doc", "read_google_sheet"],
    "1. Call list_google_docs() and list_google_sheets() to get all files.\n"
    "2. Filter by the user's search term.\n"
    "3. If the user wants content search, read matching documents.\n"
    "4. Present results with file names, IDs, and modification dates.",
    ["drive finder", "find my doc", "where is my spreadsheet", "search drive"],
    max_rounds=5, icon="search",
))

SKILLS.append(_s(
    "Notebook Brief", "Deep-dive research on a topic compiled as structured notes.",
    "Knowledge",
    ["web_search", "create_google_doc", "append_google_doc", "send_whatsapp"],
    "1. Research the topic via web_search().\n"
    "2. Create a Google Doc with sections: Overview, Key Facts, Timeline, Sources, Open Questions.\n"
    "3. Use append_google_doc() to add sections.\n"
    "4. Notify via WhatsApp with the Doc link.\n"
    "5. This is like a mini Google NotebookLM research brief.",
    ["notebook brief", "notebooklm", "research brief", "compile notes", "learn about"],
    max_rounds=8, icon="book",
))

SKILLS.append(_s(
    "Meeting Notes to Tasks", "Parse meeting notes from a Google Doc into tasks and calendar items.",
    "Knowledge",
    ["read_google_doc", "create_task", "create_calendar_event", "get_current_time"],
    "1. Call read_google_doc() on the meeting notes doc.\n"
    "2. Extract action items, deadlines, and follow-up dates.\n"
    "3. Call create_task() for each action item.\n"
    "4. Call create_calendar_event() for any scheduled follow-ups.\n"
    "5. Report what was created.",
    ["meeting notes to tasks", "action items from notes", "convert notes to tasks"],
    max_rounds=6, icon="clipboard", trigger_regex=r"\b(meeting notes|action items? from|notes? to tasks?)\b",
))


# ── Communication Hub ────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Send WhatsApp", "Send a WhatsApp message to any contact.",
    "Communication",
    ["send_whatsapp"],
    "Use send_whatsapp(to=<number>, message=<text>) to send a message. "
    "The phone number must be in international format (e.g. 14155552671 for US). "
    "Messages are limited to ~4000 characters. Be concise.",
    ["send whatsapp", "whatsapp message", "text via whatsapp"],
    max_rounds=2, icon="message",
))

SKILLS.append(_s(
    "Cross-Platform Alert", "Send the same message across WhatsApp, Telegram, and email.",
    "Communication",
    ["send_whatsapp", "send_telegram", "send_email"],
    "1. Compose the message the user wants to broadcast.\n"
    "2. Call send_whatsapp(), send_telegram(), and send_email() in sequence.\n"
    "3. Report which channels succeeded or failed.",
    ["cross-platform", "broadcast", "send everywhere", "alert all channels"],
    max_rounds=4, icon="message", trigger_regex=r"\b(cross.platform|broadcast|everywhere|all channels)\b",
))

SKILLS.append(_s(
    "WhatsApp Inbox Check", "Check email and push a summary to WhatsApp.",
    "Communication",
    ["list_emails", "get_latest_email", "send_whatsapp"],
    "1. Call get_latest_email() or list_emails() to check the inbox.\n"
    "2. Summarize key messages in 2-3 brief lines.\n"
    "3. Call send_whatsapp() with the summary.\n"
    "4. Confirm what was sent.",
    ["whatsapp inbox", "check email on whatsapp", "email to whatsapp"],
    max_rounds=4, icon="message", trigger_regex=r"\bwhatsapp\s+(inbox|check|email)\b",
))

SKILLS.append(_s(
    "Morning Briefing v2", "Full daily digest sent to both WhatsApp and Telegram.",
    "Communication",
    ["list_calendar_events", "list_tasks", "list_emails", "web_search",
     "send_whatsapp", "send_telegram", "get_current_time"],
    "1. Call list_calendar_events() for today's schedule.\n"
    "2. Call list_tasks() for pending items.\n"
    "3. Call list_emails() for unread.\n"
    "4. Call web_search() for top headlines.\n"
    "5. Compile into sections: Schedule, Tasks, Inbox, News.\n"
    "6. Send to BOTH WhatsApp and Telegram.\n"
    "7. Keep each section to 2-3 lines — be concise.",
    ["morning briefing v2", "full briefing", "daily digest all", "brief me everywhere"],
    max_rounds=10, icon="sunrise", trigger_regex=r"\b(morning briefing|full (briefing|digest)|daily digest)\b",
))


# ── Automation Setup ─────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Setup Daily Brief", "One-click: set up a daily morning briefing automation.",
    "Admin",
    ["create_automation", "send_whatsapp"],
    "1. Explain: 'I'll create a daily briefing that runs every morning at 8am.'\n"
    "2. Call create_automation() with: trigger_type='schedule', action_type='telegram', "
    "schedule={'cron': '0 8 * * *'}, task_name='daily_briefing'.\n"
    "3. Confirm the automation ID and next run time.",
    ["setup daily brief", "automate briefing", "schedule briefing", "daily automation"],
    max_rounds=4, icon="settings",
))

SKILLS.append(_s(
    "Setup News Watch", "One-click: set up a news monitoring automation for a topic.",
    "Admin",
    ["create_automation", "send_whatsapp"],
    "1. Extract the topic from the user's message.\n"
    "2. Call create_automation() with: trigger_type='schedule', action_type='telegram', "
    "schedule={'cron': '0 9,15 * * *'} (9am and 3pm), task_name='news_watch'.\n"
    "3. Confirm the automation was created.",
    ["setup news watch", "automate news", "watch topic automatically", "auto news"],
    max_rounds=4, icon="settings", trigger_regex=r"\b(setup|set up|automate)\s+(news|watch)\b",
))

SKILLS.append(_s(
    "Setup Invoice Pipeline", "One-click: set up an automated invoice processing pipeline.",
    "Admin",
    ["create_automation", "create_watcher"],
    "1. Explain: 'I'll set up a pipeline that watches your inbox for invoices, "
    "extracts them, logs them to a spreadsheet, and alerts you.'\n"
    "2. Call create_watcher() with trigger_type='email' and action_type='telegram'.\n"
    "3. Confirm the watcher was created and explain how it works.",
    ["setup invoice pipeline", "automate invoices", "invoice automation"],
    max_rounds=5, icon="settings",
))


