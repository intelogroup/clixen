# Clixen

Local LLM tools harness and chat application optimized for Apple Silicon (Apple M4, 24 GB unified memory). Runs entirely on-device with zero API costs, featuring a vanilla HTML/JS web dashboard, a launchd-managed Telegram bot, and a WhatsApp assistant bridge.

---

## Codebase Structure

```
clixen/
│
├── tools-harness/              # Active runtime — harness, agents, tools, bots, UI
│   ├── core.py                 # Consolidated process: chat_ui + telegram + task_worker
│   ├── harness.py              # Main orchestrator — run() / local_chat()
│   ├── _harness_fs_actions.py  # LocalFsAction parsing/execution
│   ├── _harness_dispatch_render.py # DispatchResult → user-facing string
│   ├── chat_ui.py              # FastAPI server, ~70 routes (HTML/CSS/JS in static/ + templates/)
│   ├── telegram_bot.py         # Telegram interface (launchd-managed)
│   ├── whatsapp_bot.py         # WhatsApp bot (launchd-managed)
│   ├── whatsapp_bridge.js/ts   # Baileys Node.js gateway
│   ├── brabble_hook.py         # Wake-word voice agent hook
│   ├── clixen_ptt.py           # Push-to-talk voice interface
│   ├── doctor.py               # System diagnostics
│   ├── config.py               # App configuration
│   │
│   ├── agents/                 # LangGraph agent definitions + specialists
│   │   ├── local_agent_graph.py# Graph definition
│   │   ├── local_agent_nodes.py# Node functions + pipeline hints
│   │   ├── local_agent_state.py# State model
│   │   ├── local_agent_tools.py# Filtered toolset for local models
│   │   ├── specialists/        # 10 task-specific subagents
│   │   │   ├── dispatch.py    #   Routes to run_*_specialist()
│   │   │   ├── audio_specialist.py
│   │   │   ├── data_specialist/#   Package: _helpers/_stats/_advanced/_schemas/agent.py
│   │   │   ├── form_specialist.py
│   │   │   ├── path_specialist.py
│   │   │   ├── read_specialist.py
│   │   │   ├── research_specialist.py
│   │   │   ├── scraper_specialist.py
│   │   │   ├── transport_specialist.py
│   │   │   ├── video_specialist.py
│   │   │   └── write_specialist.py
│   │   ├── nodes/
│   │   │   └── wizard/        # (empty — wizard node placeholder)
│   │   └── static/charts/     # Diagram assets
│   │
│   ├── clients/                # LLM connections + prompt templates
│   │   ├── ollama_client.py    # Local Ollama HTTP API + tool loops
│   │   ├── cloud_client.py     # OpenRouter (DeepSeek primary, Claude Haiku fallback)
│   │   ├── router.py           # classify()/classify_telegram()/classify_ide()
│   │   ├── router_patterns.py  # Regex constants for router.py
│   │   ├── cancellation.py     # Stream cancellation support
│   │   └── cost_guard.py       # Token/cost tracking
│   │
│   ├── store/                  # Persistence layer
│   │   ├── sessions/           # JSON files per chat_id
│   │   ├── conversation.py     # Thread-safe sliding window manager
│   │   ├── workflow_store.py   # Scheduled automations (SQLite, APScheduler cron)
│   │   ├── automation_store.py # Automation presets
│   │   ├── knowledge_base.py   # Semantic search index (LanceDB)
│   │   ├── trace_store.py      # Agent execution tracing
│   │   ├── raw_store.py        # Raw message storage
│   │   ├── chunker.py          # Text chunking for KB
│   │   ├── upload_store.py     # File upload metadata
│   │   └── db_discovery.py     # DB schema introspection
│   │
│   ├── jobs/                   # Background task workers
│   │   ├── worker.py           # Single poll loop (core.py thread)
│   │   ├── job_queue.py        # One-shot tasks (SQLite, claim_next())
│   │   ├── handler_registry.py # automation_id → handler dispatch
│   │   └── handlers/           # 10 builtin + user_automation.py
│   │       ├── briefing_morning.py
│   │       ├── tech_brief.py
│   │       ├── email_daily_summary.py
│   │       ├── email_watch_sender.py
│   │       ├── email_attachment_watch.py
│   │       ├── email_attachment_archive.py
│   │       ├── inbox_monitor_attachments.py
│   │       ├── study_usmle_daily.py
│   │       ├── golden_queries.py
│   │       └── user_automation.py  # Generic action_type dispatch: telegram/notification/
│   │           # http_webhook/email/tool_call/imessage. tool_call runs one browser-tagged
│   │           # connector tool (DoorDash/Uber/BrowserOS) on a schedule/webhook; imessage
│   │           # supports plain send or watch_contact+reply_message with rule gating
│   │           # (blackout_days, skip_keywords, blacklist_locations, earliest_start_hour,
│   │           # check_calendar w/ 1hr-buffer conflict check via gcalendar.check_calendar_conflict)
│   │
│   ├── tools/                  # ~100 tool implementations
│   │   ├── registry.py         # ALL_TOOLS / EXECUTORS aggregator
│   │   ├── _registry/          # imports.py + schemas.py + executors.py
│   │   ├── websearch.py        # Web search orchestrator (guard→rewrite→search→rerank→summarize)
│   │   ├── search_agentic/     # _scoring.py + _summarize.py
│   │   ├── searxng_search.py   # Local SearXNG
│   │   ├── ddg_search.py       # DuckDuckGo scraper
│   │   ├── browseros_search.py # BrowserOS CLI fallback
│   │   ├── brave_search.py     # Brave Search API
│   │   ├── fast_search.py      # Fast cached search
│   │   ├── connector_uber.py   # Uber ride estimates
│   │   ├── connector_doordash.py
│   │   ├── connector_sofascore.py
│   │   ├── connector_ringback.py # call_my_phone() — real SIP call, 900s cooldown
│   │   ├── scrapling_fetch.py  # Adaptive web scraping
│   │   ├── browser.py          # Headless browser (Playwright)
│   │   ├── session_browser.py  # Persistent browser sessions
│   │   ├── vision_browser.py   # Vision-enabled browser
│   │   ├── docling_tool.py     # Document parsing
│   │   ├── pdf_tools.py        # PDF generation/manipulation
│   │   ├── flat_pdf_tools.py   # Fast PDF text extraction
│   │   ├── docx_tools.py       # DOCX generation
│   │   ├── gdocs.py / gdocs_tool.py     # Google Docs
│   │   ├── gsheets.py / gsheets_tool.py # Google Sheets
│   │   ├── gmail.py            # Gmail send/search
│   │   ├── gcalendar.py        # Google Calendar; check_calendar_conflict() — point-in-time
│   │   │                       #   conflict check w/ buffer, used by automation accept/decline gating
│   │   ├── gtasks.py           # Google Tasks
│   │   ├── google_auth.py      # OAuth flow
│   │   ├── email_parse.py / email_attachments.py
│   │   ├── slack_search.py     # Slack search
│   │   ├── whatsapp_tool.py / whatsapp_search.py
│   │   ├── telegram_send.py
│   │   ├── imessage_search.py  # search/send/list_new_from (~/Library/Messages/chat.db read + Messages.app send).
│   │   │                       #   Self-sends route through the existing self-chat by `chat id` (no "buddy"
│   │   │                       #   relationship with your own account); `from_`/IMMESSAGE_DEFAULT_SENDER is
│   │   │                       #   only for self-send detection, never fed into AppleScript `account "..."`
│   │   │                       #   (accounts are UUID-keyed, not phone-number-keyed — that was a live bug).
│   │   ├── macos_native.py     # macOS-native integrations
│   │   ├── spotlight.py        # macOS Spotlight search
│   │   ├── filesystem.py       # File read/write/search
│   │   ├── shell.py            # Shell command execution
│   │   ├── git.py              # Git operations
│   │   ├── repl.py             # Python REPL
│   │   ├── arxiv_search.py / arxiv_tool.py
│   │   ├── pubmed.py / pubmed_tool.py
│   │   ├── github_search.py
│   │   ├── youtube_tool.py     # YouTube transcript
│   │   ├── livescore.py        # Live sports scores
│   │   ├── bus_eta.py          # MBTA transit predictions
│   │   ├── inflight_tracker.py # Flight tracking
│   │   ├── audio_tools.py / audio.py / voiceprint.py
│   │   ├── image_edit.py / image_generation.py
│   │   ├── video_tools.py
│   │   ├── ocr.py / surya_ocr.py / local_vision.py
│   │   ├── automation_tools.py # create/list/get/update/pause/resume/delete/trigger_automation_now/
│   │   │                       #   delete_workflow_permanently. action_type enum: telegram/
│   │   │                       #   notification/http_webhook/email/tool_call/imessage — keep the
│   │   │                       #   3 schemas (create/update/list) and harness.py's automation-intent
│   │   │                       #   system prompt in sync with this enum, or the agent can build
│   │   │                       #   an automation type its own tool-calling can't recreate later.
│   │   ├── workflow_job_tools.py
│   │   ├── memory_tools.py / reminder.py / reminders.py
│   │   ├── file_tracker.py / semantinc_files.py
│   │   ├── office_tools.py     # Office suite automation
│   │   ├── form_tools.py / vision_form_tools.py
│   │   ├── confirmtion.py      # User confirmation
│   │   ├── create_watcher.py   # File watcher creation
│   │   ├── deep_research.py / report_generator.py
│   │   ├── diagram_render.py   # Mermaid diagram rendering
│   │   ├── path_policy.py       # Path sanitization
│   │   ├── peakaboo.py         # Quick file peek
│   │   ├── discovery_sources.py# Search source discovery
│   │   ├── followup.py         # Follow-up suggestion
│   │   ├── injection_guard.py  # Prompt injection defense
│   │   ├── premise_check.py    # Query premise validation
│   │   ├── query_guard.py      # Clarification check
│   │   ├── tool_failure_log.py # Failure logging
│   │   ├── agent_reach.py      # Social media research
│   │   ├── fetch_url.py        # URL content fetch
│   │   ├── context7.py         # Context7 API
│   │   ├── local_search.py     # Local file search
│   │   ├── system_status.py    # System monitoring
│   │   ├── time_tool.py        # Time/date info
│   │   ├── vault.py            # Secure credential storage
│   │   ├── structured.py       # Structured output
│   │   ├── api_client.py       # Generic API client
│   │   ├── document_create.py  # Document generation
│   │   ├── notifications.py    # System notifications
│   │   └── clutter_tools.py    # Misc utilities
│   │
│   ├── skills_hub.py           # Skill scoring/matching/dispatch
│   ├── skills_data/            # ~65 Skill definitions
│   ├── workflows/              # Automation workflow definitions
│   │   ├── daily_digest.py
│   │   ├── form_forwarder.py
│   │   ├── invoice_tracker.py
│   │   ├── lead_alert.py
│   │   └── meeting_prep.py
│   │
│   ├── jobs/                   # Background job handlers (see above)
│   ├── evals/                  # Agent evaluation suite
│   │   ├── cases.jsonl         # Test cases
│   │   ├── test_agent_live.py
│   │   ├── test_invariants.py
│   │   └── test_routing_offline.py
│   │
│   ├── benchmarks/             # Performance benchmarks + reports
│   │   ├── benchmark_50q.py   # 50-question benchmark
│   │   ├── benchmark_agentic_pipeline.py
│   │   ├── benchmark_search_hard.py
│   │   ├── benchmark_conversation.py
│   │   ├── bench_ddg.py       # DDG-specific benchmarks
│   │   ├── bench_summarizer.py
│   │   ├── search_profiler_results.json
│   │   └── *.json / *.md      # Reports
│   │
│   ├── scripts/                # Utility scripts
│   │   ├── daily_email_summary.py
│   │   ├── email_watch.py      # Email monitoring
│   │   ├── enroll_voice.py     # Voiceprint enrollment
│   │   ├── fix_stale_kb.py     # KB maintenance
│   │   ├── warmup_ollama.py    # Model warmup
│   │   ├── profile_hardware.py # HW profiling
│   │   └── serve_sidecar.py    # Sidecar server
│   │
│   ├── launchd/                # launchd plists for background services
│   │   ├── com.clixen.daily_email_summary.plist
│   │   ├── com.clixen.email_watch_*.plist (×3)
│   │   └── com.clixen.task_worker.plist
│   │
│   ├── templates/              # FastAPI Jinja2 templates
│   │   ├── index.html
│   │   ├── landing.html
│   │   └── login.html
│   ├── static/                 # Frontend assets
│   │   ├── app.js / chat.js / ide.js / voice.js
│   │   ├── base.css / chat.css / ide.css / sidebar.css
│   │   ├── auth.js / utils.js
│   │   └── charts/
│   │
│   ├── kokoro_daemon.py        # TTS warm daemon
│   ├── voiceprint_daemon.py    # Speaker verification daemon
│   └── ringback/                # SIP voice-call docker source + MCP server (own repo, vendored)
│       ├── src/voice_mcp.py    # MCP server: call_start/call_end tools
│       └── .last_call_ts       # Cooldown state, read by tools/connector_ringback.py
│
├── docs/                       # Documentation
│   ├── agents/                 # Agent development internals
│   └── superpowers/plans/      # Design docs
│
├── cloudflare-worker/          # Cloudflare Workers (DDG proxy, Telegram proxy, ReliefWeb)
├── google-mcp/                 # Google APIs MCP server (Node.js)
│   ├── server.js / auth.js
│   └── download-attachments.mjs / download-to-repo.mjs
│
├── models/                     # Local ML model weights
│   ├── ggml-*.bin              # Whisper models (tiny → large-v3)
│   ├── kokoro-v1.0.onnx        # TTS model
│   ├── voices-v1.0.bin         # Voice embeddings
│   ├── multilingual-e5/        # Embedding model
│   └── mxbai/                  # Embedding model
│
├── data/                       # Application data
│   └── research_reports/
│
├── scripts/                    # Root-level utility scripts
│   ├── batch_distill.py
│   ├── benchmark_qwen.py
│   ├── clixen_shot.mjs / clixen_shot.py
│   └── generate_trial_data.py
│
├── tests/                      # Test files (24 test modules)
│   ├── test_harness_local_agent_dispatch.py
│   ├── test_telegram_routing_sweep*.py (×6)
│   ├── test_automation_catalog.py
│   ├── test_registry_validation.py
│   └── ...
│
├── graphify-out/               # Codebase knowledge graph artifacts
├── output/                     # Generated output files
│
├── pyproject.toml              # Build config + dependency declarations
├── Makefile                    # Target shortcuts
├── CLAUDE.md                   # Agent guide (stack, routing, model roster)
├── AGENTS.md                   # Agent development guide
├── GEMINI.md                   # Gemini-specific notes
└── CHANGELOG.md                # Version history
```

**Known Issues**: `chat_ui.py` and `telegram_bot.py` stay as single large files on purpose — their
route handlers/agent callbacks are coupled to mutable module-level state (`_workspace_state`,
`_active_streams`, `_kokoro`, `_whisper`, `_scheduler`) that tests patch directly via
`monkeypatch.setattr(chat_ui, "_workspace_state", ...)`. Moving that code to submodules would
require every handler to do dynamic `chat_ui.<attr>` lookups instead of normal imports to keep
those patches working — judged higher risk than the size win. Same reasoning applies to
`harness.py`'s `run()`, which is why it's still ~1000 lines on its own.

---

## Dataflow

```
                     +---------------------------------------+
                     |             User Interfaces           |
                     |  - Web UI (http://localhost:9234)     |
                     |  - Telegram Bot                       |
                     |  - WhatsApp Bot (via Node Bridge)     |
                     +-------------------+-------------------+
                                         |
                                         v [User Query]
                     +-------------------+-------------------+
                     |          FastAPI / Bot Server         |
                     +-------------------+-------------------+
                                         |
                                         v [Classify Intent]
                     +-------------------+-------------------+
                     |         Intent Classifier             |
                     |   (tools-harness/clients/router.py)   |
                     |   - Heuristic Regex Matches           |
                     |   - Model Selection (cloud-first;      |
                     |     gemma4 only for ocr + a few        |
                     |     harness.py-level overrides)        |
                     +-------------------+-------------------+
                                         |
                       +-----------------+-----------------+
         [Casual/General Chat] |                             | [Agentic/Tool Chaining]
                               v                             v
               +---------------+---------------+     +-------+---------------+
               |    Direct LLM Chat Loop       |     |     Agent Execution   |
               | - Load History                |     |   (harness.run / LG)  |
               | - Evict/Compact old turns     |     +-------+---------------+
               | - Stream response to Client   |             |
               +---------------+---------------+             v [Check/Extract Tools]
                               |                     +-------+---------------+
                               |                     |    Registry / Tools   |
                               |                     | - Execute local tools |
                               |                     | - Apply pipeline hints|
                               |                     +-------+---------------+
                               |                             |
                               |                             v [Websearch Tool]
                               |                     +-------+---------------+
                                |                     |   Web Search Graph    |
                                |                     | 1. Guard check        |
                                |                     | 2. Parallel Search    |
                                |                     |    (SearXNG + DDG)    |
                                |                     | 3. Rerank snippets    |
                                |                     | 4. Summarize (gemma4) |
                                |                     | 5. Finalize output    |
                                |                     +-------+---------------+
                                |                             |
                                +--------------+--------------+
                                               |
                                               v [Direct LLM or Tool Interaction]
                                      +--------+---------+
                                      | OpenRouter Cloud |
                                      | (DeepSeek, w/    |
                                      |  Claude Haiku    |
                                      |  fallback)       |
                                      +--------+---------+
                                               |
                                               v [cloud unreachable —
                                               |  local_chat() catches
                                               |  the exception]
                                      +--------+---------+
                                      |  Local Ollama    |
                                      |  (gemma4, fallback|
                                      |   + ocr/vision)  |
                                      +------------------+
```

---

## Documentation

- **Agent Internals**: [docs/agents/](docs/agents/)

Deeper reference docs for how the agent stack actually works, split out of `CLAUDE.md` to keep that file focused on day-to-day gotchas:

| Doc | Covers |
|-----|--------|
| [orchestrator.md](docs/agents/orchestrator.md) | Top-level Orchestrator Agent — replaced the old rigid intent-classification cascade |
| [models.md](docs/agents/models.md) | Cloud-first routing revamp, model roster, fallback rules |
| [automations.md](docs/agents/automations.md) | `jobs/worker.py` poll loop — one-shot `job_queue` + scheduled `workflow_store`, both dispatch paths |
| [local-agent-tools.md](docs/agents/local-agent-tools.md) | Task-scoped LangGraph local-agent toolset (`agents/local_agent_tools.py`) |
| [browser-automation.md](docs/agents/browser-automation.md) | 3 browser-automation approaches + local vision for screenshots |
| [filesystem-routing.md](docs/agents/filesystem-routing.md) | 4-tier dispatch for `intent="filesystem"` in `harness.py:run()` |
| [conversation-history.md](docs/agents/conversation-history.md) | Sliding-window chat history + compaction (`store/conversation.py`) |
| [skills-hub.md](docs/agents/skills-hub.md) | `skills_hub.py` skill-matching scoring (4 signals) |
| [voice-brabble.md](docs/agents/voice-brabble.md) | Brabble wake-word voice agent |
| [web-search.md](docs/agents/web-search.md) | Web search pipeline internals |

---

## Active Fleet & Models

**Cloud-first (2026-07)**: the main agent (`harness.py` and the LangGraph local-agent) defaults
to a cloud model via OpenRouter, not gemma4 — gemma4 was unreliable past 2-3 chained tool calls.
The only local-only carve-out is the `ocr` intent (no multimodal support in the cloud client).

| Model | Where | Role |
|-------|-------|------|
| `deepseek/deepseek-v4-flash` | Cloud (DeepSeek direct API) | **Default** for the main agent — chat + agentic tool use |
| `openrouter/anthropic/claude-haiku-4.5` | Cloud (OpenRouter) | Automatic fallback on error or after 3 consecutive tool errors |
| `gemma4:12b-mlx` | Local, 7.6 GB, warm | OCR/vision carve-out, intent classifier, manual override |
| `gemma4:e2b` | Local, 7.2 GB | Tertiary: warmed but not primary |
| `qwen3.5:4b` | Local, 3.4 GB | Query rewriting (websearch), chat-history compaction, lightweight classification |
| `qwen3-vl:8b` / `qwen3-vl:4b` | Local | Vision (OCR, browser screenshot analysis) |
| `granite3.3:2b`| Local, 1.5 GB | Lightweight tasks / miscellaneous |

### Performance Optimization
* **KV Cache Pre-allocation**: Pre-allocated at 16384 context length during warmup (`clients/ollama_client.py`) to reduce first-request latency from 15s to ~0.7s.
* **No `think=False` for Gemma4**: The websearch summarizer requires gemma4's thinking mode enabled to produce structured answers; `<think>` tags are stripped post-hoc. For fast agentic tool loops with qwen3:8b, `think=False` is used.
* **Search Fallback Order**: Fast scraped DDG search runs first, falling back to BrowserOS CLI search only if scraped DDG fails due to rate limiting or challenges. This reduces base latency by ~3.5s.

---

## Quick Start

### 1. Requirements
Ensure you have Docker running (for SearXNG) and Ollama installed.

### 2. Production: consolidated core service
`core.py` runs `chat_ui` + `telegram_bot` + `email_watch` + `task_worker` (background job
queue + scheduled automations) as threads in **one** process — this is what's actually
running day-to-day, managed by launchd:
```bash
launchctl load ~/Library/LaunchAgents/com.clixen.core.plist
# after editing any code core.py imports, restart the whole process (threads share it —
# a file edit needs a full process restart, not just a thread respawn):
launchctl kickstart -k gui/$(id -u)/com.clixen.core
```
Open [http://localhost:9234](http://localhost:9234) in your browser.

### 3. Dev: running pieces individually
```bash
# Start local SearXNG
docker run -d --name searxng -p 8888:8080 searxng/searxng

# Start just the Web UI server (no Telegram/task_worker)
cd tools-harness
python chat_ui.py
```

### 4. WhatsApp + Telegram (single supervised job)
Both bots run under one launchd job (not `core.py`, not separate plists per bot):
```bash
launchctl load ~/Library/LaunchAgents/com.clixen.messaging.plist
```
This runs `tools-harness/messaging_supervisor.sh`, which supervises `telegram_bot.py`,
`whatsapp_bot.py`, and `whatsapp_bridge.js` (Node/Baileys) as one process group.

### 5. Voice (Brabble)
```bash
launchctl load ~/Library/LaunchAgents/com.brabble.agent.plist
```
Wake word → `brabble_hook.py` (cold subprocess, POSTs to the already-warm `chat_ui.py`
server rather than cold-importing the harness) → streamed reply spoken sentence-by-sentence.
