"""Skill definitions — split out of skills_hub.py by category. See skills_hub.py for Skill/_s/SKILLS."""

from __future__ import annotations

from skills_hub import SKILLS, _s

# ── Inbox ─────────────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Check Inbox", "Show the most recent unread emails with sender and subject.",
    "Inbox",
    ["get_latest_email"],
    "Call get_latest_email() to fetch the latest email. Show sender, subject, and a brief summary. "
    "If the user asked for more than one email, also call list_emails(). Be concise.",
    ["check email", "inbox", "new mail", "unread", "latest email", "what's in my inbox"],
    max_rounds=3, icon="mail",
))

SKILLS.append(_s(
    "Summarize Email", "Summarize the latest email and optionally send via Telegram.",
    "Inbox",
    ["get_latest_email", "send_telegram"],
    "STEP 1: Call get_latest_email() to fetch the full email body. "
    "STEP 2: Write a concise summary (sender, subject, main point, action needed) under 100 words. "
    "STEP 3: If the user wants it sent to Telegram, call send_telegram(message=<your summary>).",
    ["summarize email", "email summary", "summarize my mail", "brief on email"],
    max_rounds=4, icon="mail",
))

SKILLS.append(_s(
    "Send Email Reply", "Draft and send a reply to the latest or specified email.",
    "Inbox",
    ["get_latest_email", "send_email", "list_emails", "read_email"],
    "First call get_latest_email() or list_emails() to find the right thread. "
    "Then call send_email() with recipient, subject, and body. "
    "Confirm the message was sent. If auth fails, call refresh_google_token() first.",
    ["reply to email", "send reply", "respond to email", "compose email", "reply to the latest"],
    max_rounds=6, icon="mail", trigger_regex=r"\breply\s+to\b",
))

# ── Calendar ───────────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Today's Schedule", "List all calendar events for today.",
    "Calendar",
    ["list_calendar_events", "get_current_time"],
    "Call list_calendar_events() and format today's events clearly with times and titles. "
    "If there are no events, say so. Show the current time for context.",
    ["today's schedule", "what's on my calendar", "today's meetings", "appointments today", "calendar today"],
    max_rounds=3, icon="calendar",
))

SKILLS.append(_s(
    "Add Calendar Event", "Create a new calendar event at a specific date/time.",
    "Calendar",
    ["create_calendar_event", "get_current_time"],
    "Extract event title, date, time, duration, and description from the user's message. "
    "Call create_calendar_event() with those details. Confirm the event was created.",
    ["add event", "create meeting", "schedule", "new calendar event", "put on calendar"],
    max_rounds=4, icon="calendar", trigger_regex=r"\b(create|add|new|make)\s+(a\s+)?(meeting|event|appointment|calendar)\b",
))

SKILLS.append(_s(
    "Calendar Briefing", "Briefing on upcoming events for today and tomorrow.",
    "Calendar",
    ["list_calendar_events", "get_current_time", "send_telegram"],
    "Call list_calendar_events() and check today and tomorrow. "
    "Identify conflicts, prep needs, and next actions. "
    "If the user wants a Telegram recap, call send_telegram() with a concise briefing.",
    ["calendar briefing", "briefing", "what's coming up", "this week", "what do I have this week", "upcoming events"],
    max_rounds=5, icon="calendar",
))

# ── Tasks ──────────────────────────────────────────────────────────────────

SKILLS.append(_s(
    "List Tasks", "List current Google Tasks, optionally filtered.",
    "Tasks",
    ["list_tasks", "get_current_time"],
    "Call list_tasks() and show the user's current task list. "
    "If the user asks to filter, describe the relevant tasks. "
    "If no tasks exist, say so.",
    ["list tasks", "show tasks", "what are my tasks", "my to-do", "todo list"],
    max_rounds=3, icon="clipboard",
))

SKILLS.append(_s(
    "Create Task", "Create a new task in Google Tasks.",
    "Tasks",
    ["create_task", "get_current_time"],
    "Extract task title and optional notes/due date from the user's message. "
    "Call create_task() with those details. Confirm the task was created. "
    "If auth fails, call refresh_google_token() first, then retry.",
    ["create task", "add task", "new task", "remind me to", "add to-do"],
    max_rounds=4, icon="clipboard", trigger_regex=r"\b(create|add|new|make)\s+(a\s+)?(task|to-do|todo)\b",
))

SKILLS.append(_s(
    "Complete Task", "Mark one or more tasks as completed.",
    "Tasks",
    ["list_tasks", "complete_task"],
    "First call list_tasks() to find the task ID. "
    "Then call complete_task() with the right task_id. "
    "Confirm the task was marked complete.",
    ["complete task", "finish task", "mark done", "task done", "check off"],
    max_rounds=5, icon="clipboard",
))

# ── Web Search ─────────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Web Search", "Search the web for current information, news, prices, or facts.",
    "Search",
    ["web_search"],
    "You have access to web_search. Your training data has a cutoff of approximately late 2024. "
    "For ANY question about schedules, upcoming events, prices, versions, recent news, "
    "recent results, match scores, standings, tournament winners, legal rulings, "
    "merger decisions, space missions, court cases, or announcements from 2025 or later: "
    "call web_search FIRST, then base your answer ONLY on what the search results say. "
    "NEVER answer from memory for events after late 2024.",
    ["search", "look up", "google", "find info", "news", "price", "weather",
     "score", "who won", "what happened", "recent", "latest", "current", "2025", "2026"],
    max_rounds=3, icon="radar",
))

# ── Browser ────────────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Browse Web", "Navigate a website, inspect content, or fill forms.",
    "Browser",
    ["browser_navigate", "browser_snapshot", "browser_get_url", "browser_click",
     "browser_type", "browser_wait", "browser_get_content", "browser_screenshot"],
    "You are a browser automation agent. You control a headless Chromium browser using tool calls.\n"
    "DISCOVERY: Always call browser_snapshot() after navigate or click to see the current page structure.\n"
    "Never guess selectors — read them from the snapshot.\n"
    "If checkboxes/buttons don't respond to browser_click(), use browser_run_js() with jQuery.\n"
    "After form submits that trigger redirects, call browser_get_url() to confirm where you landed.\n"
    "Never call browser_close(). Complete ALL steps before reporting back.",
    ["browse", "open website", "go to", "navigate to", "login to", "fill form",
     "check website", "screenshot", "visit"],
    max_rounds=15, icon="browser",
))

# ── Filesystem ─────────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Read File", "Read the contents of a file or document.",
    "Files",
    ["read_file", "read_document", "grep_files", "list_directory"],
    "For plain text/code files: call read_file(path, offset, limit). Use grep_files first to find exact lines if needed.\n"
    "For PDFs, DOCX, images, configs: call read_document(path).\n"
    "For a directory listing: call list_directory(path).\n"
    "Return the file content directly with no wrapper text.",
    ["read file", "open file", "show file", "cat", "what's in", "show contents"],
    max_rounds=4, icon="file",
))

SKILLS.append(_s(
    "Search Files", "Search for files by name or grep for text patterns.",
    "Files",
    ["find_files", "grep_files", "read_file", "list_directory", "file_tree"],
    "Use find_files(pattern, path) to locate files by name glob.\n"
    "Use grep_files(pattern, path) to search inside files.\n"
    "Use list_directory(path) to browse folders.\n"
    "Use file_tree(path) for a recursive view.\n"
    "Present results clearly with file paths and matching lines.",
    ["find file", "search files", "locate", "grep", "find text", "where is", "what files", "show files in", "list my files", "list files"],
    max_rounds=5, icon="search",
))

SKILLS.append(_s(
    "Write File", "Create a new file or overwrite an existing one.",
    "Files",
    ["write_file", "read_file"],
    "Call write_file(path, content) to create or overwrite a file. "
    "Use absolute paths. Confirm the file was written. "
    "If the user wants to modify an existing file, read it first.",
    ["write file", "create file", "save", "new file", "save as"],
    max_rounds=3, icon="edit",
))

SKILLS.append(_s(
    "Delete File", "Delete a file or directory.",
    "Files",
    ["delete_file", "list_directory"],
    "First confirm the path with the user if it's ambiguous. "
    "Call delete_file(path, recursive=True) for directories. "
    "Confirm what was deleted.",
    ["delete file", "remove file", "rm", "delete folder", "remove directory", "delete the file"],
    max_rounds=3, icon="trash", trigger_regex=r"\b(delete|remove|rm|erase)\b",
))

# ── Code ───────────────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Run Python", "Execute Python code and return the output.",
    "Code",
    ["run_python", "write_file", "read_file"],
    "Write the Python code to a file with write_file(), then execute it with run_python(). "
    "If the code is short (< 20 lines), run it directly. "
    "Return the output concisely.",
    ["run python", "execute python", "python script", "run code"],
    max_rounds=4, icon="code",
))

SKILLS.append(_s(
    "Run Shell", "Execute a bash command and return the output.",
    "Code",
    ["bash_exec", "write_file", "read_file"],
    "Write the script to a file if multi-line, then execute with bash_exec(). "
    "For simple commands, run bash_exec() directly. "
    "Return the output concisely.",
    ["run bash", "shell", "bash command", "terminal", "execute command"],
    max_rounds=4, icon="terminal",
))

# ── Git ────────────────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Git Status", "Show the current git working tree status.",
    "Git",
    ["git_status", "git_diff", "git_log", "read_file"],
    "Call git_status() to show the current state. "
    "If the user wants to see changes, call git_diff(). "
    "If the user wants history, call git_log().",
    ["git status", "git diff", "what changed", "git log", "commit history", "show changes"],
    max_rounds=4, icon="git",
))

SKILLS.append(_s(
    "Git Commit", "Stage and commit changes with a message.",
    "Git",
    ["git_status", "git_diff", "git_add", "git_commit", "read_file"],
    "1. Call git_status() to see changes.\n"
    "2. If the user didn't specify files, review git_diff() and commit everything.\n"
    "3. Call git_add(files=['file1', ...]) to stage.\n"
    "4. Call git_commit(message='descriptive message') to commit.\n"
    "Confirm what was committed.",
    ["git commit", "commit changes", "stage and commit", "commit my", "commit this", "commit code"],
    max_rounds=5, icon="git",
))

# ── Documents ──────────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Summarize Document", "Read a document and produce a concise summary.",
    "Documents",
    ["read_document", "read_file", "send_telegram"],
    "1. Call read_document(path) to load the content.\n"
    "2. Produce a concise summary: title, key points, action items.\n"
    "3. If the user wants a Telegram notification, call send_telegram().",
    ["summarize document", "summarize pdf", "summarize doc", "summary of file"],
    max_rounds=5, icon="book",
))

SKILLS.append(_s(
    "Convert Document", "Convert a document to another format (DOCX, PDF, XLSX, PPTX).",
    "Documents",
    ["read_document", "read_file", "write_file"],
    "Read the source document with read_document(). "
    "Use the document creation tools to convert to the requested format. "
    "Save to the specified output path.",
    ["convert document", "convert to pdf", "convert to docx", "export", "convert pdf"],
    max_rounds=5, icon="book", trigger_regex=r"\bconvert\s+(to|from|this|the|my)",
))

# ── Telegram / Messaging ───────────────────────────────────────────────────

SKILLS.append(_s(
    "Send Telegram", "Send a message to the user via Telegram.",
    "Messaging",
    ["send_telegram"],
    "Call send_telegram(message='your message here'). "
    "Keep messages under 200 words. Be concise. "
    "Confirm the message was sent.",
    ["send telegram", "telegram me", "message me", "ping me", "notify me"],
    max_rounds=2, icon="message",
))

