# Orchestrator Agent Architecture (2026-07)

The clixen routing system has been transitioned from a rigid, context-blind intent classification cascade to a **top-level Orchestrator Agent**.

## 1. Structure & Mechanics
- **Model**: Cloud-based (`deepseek/deepseek-v4-flash` or `openrouter/anthropic/claude-haiku-4.5`).
- **Context-Awareness**: Every user message is evaluated along with the **last 5 turns of conversation history**. Pronouns, deictic references, and implicit instructions (like *"Same for infochir"*) are resolved using history before invoking subagents.
- **Orchestration**: The Orchestrator runs by default on all message paths (Web, Telegram, WhatsApp, voice/Brabble) except plan mode or direct tool/search bypasses.
- **Subagents as Tools**: The Orchestrator is equipped with **28** high-level subagent/direct tools (grew from the original 7 — this list is the current one in `orchestrator_tools.py`, don't trust an older count):
  - `ask_local_agent(query)`: Complex multi-step filesystem operations, PDF/DOCX form parsing, run Python.
  - `ask_read_file` / `ask_write_file` / `ask_delete_file` / `ask_rename_file` / `ask_run_command`: fast, no-LLM direct filesystem/shell tools.
  - `ask_web_search(query)`: Search internet for recent events/schedules/news post-2024.
  - `ask_browser_agent(query)`: Browser automation (Playwright/BrowserOS, DoorDash/Uber/banking).
  - `ask_macos_native(query)`: Clipboard, Safari tabs, macOS Notes, AppleScript.
  - `ask_email_agent(query)`: Checking, reading, summarizing, and sending emails.
  - `ask_tasks_agent(query)`: Creating/completing/listing/deleting tasks.
  - `ask_calendar_agent(query)`: Managing calendar events.
  - `ask_docs_agent(query)` / `ask_sheets_agent(query)`: Google Docs / Sheets.
  - `ask_deep_research(query, depth, breadth)`: Multi-step parallel research report.
  - `ask_automation_agent(query)`: Create/list/pause/resume/delete/trigger scheduled automations — see [automations.md](automations.md).
  - `ask_dev_agent(query)`: Git ops, REPL/window inspection.
  - `ask_messaging_agent(query)`: iMessage/WhatsApp/Slack search+send, reminders.
  - `ask_research_agent(query)`: Structured/academic lookups (Wikipedia, Wikidata, Crossref, OpenAlex, SEC EDGAR, GDELT, sports scores), stealthy scraping, library docs.
  - `ask_vision_agent(query)`: Screenshot, OCR, visual form fill, Spotlight search.
  - `ask_utility_agent(query)`: System health, geocoding, local image gen, directory organization.
  - `ask_transport_agent(query)` *(added 2026-07)*: Real bus/transit ETAs (`bus_eta`) or Uber ride estimates/trip history.
  - `ask_youtube_agent(query)` *(added 2026-07)*: Search YouTube or fetch video transcripts.
  - `ask_reddit_agent(query)`: Reddit search/read/summarize.
  - `ask_x_agent(query)`: X/Twitter search/read.
  - `ask_science_scout_agent(query)`: PubMed/arXiv paper monitoring and triage.
  - `ask_send_message(query)`: Direct outbound message send (Telegram/WhatsApp/etc).
  - `ask_fetch_url(url)`: Direct URL fetch, no agent loop.

## 2. Implementation Files
- **[harness.py](file:///Users/kalinovdameus/Developer/clixen/tools-harness/harness.py)**: Houses `ORCHESTRATOR_SYSTEM_PROMPT` and intercepts execution in `run()` to invoke the coordinator agent when `orchestrated=True`.
- **[orchestrator_tools.py](file:///Users/kalinovdameus/Developer/clixen/tools-harness/tools/orchestrator_tools.py)**: Declares schemas and executors for all 28 subagent tools, propagating thread-safe session IDs via `CURRENT_CHAT_ID`.
- **[registry.py](file:///Users/kalinovdameus/Developer/clixen/tools-harness/tools/registry.py)**: Declares `CURRENT_CHAT_ID` (`contextvars.ContextVar`) and registers the subagents.

## 3. Optimization (Bypassed Latency)
- Telegram and WhatsApp bots run a quick regex checking for potential document creation requests (`_run_doc_agent`). For all other standard queries, they bypass the local `classify_message` call entirely, saving **2-5 seconds** of local classification model latency.
