# Clixen — Project Guide for Claude

## What This Is
Local LLM harness + chat app, on-device (Apple M4, 24 GB unified memory).
- **Web UI**: http://localhost:9234 — vanilla HTML/JS, FastAPI, SSE streaming
- **Telegram + WhatsApp bots**: one launchd job supervise both
- **Voice**: Brabble (wake-word)
- **Repo vs package name**: repo dir `clixen`; launchd label prefix renamed `com.clixen.*` (2026-08-03)
- **`AGENTS.md`**: deeper agent-dev reference — tool inventory, specialist dispatch, browser automation, connectors, voice. Points into `docs/agents/*.md` for full depth.
- **Standalone script runs**: use `~/Developer/clixen/.venv/bin/python`, not system python — has `mcp` + all deps; system python too old/missing packages for some modules.
- **Type check**: `npm run typecheck` (`tsc --noEmit`) — run after any `.ts`/`.tsx` edit, no build step needed.
- **Blender knowledge base**: `~/.claude/skills/blender-motion/references/*.md` (bpy gotchas, GN scatter, materials, shading) indexed into LanceDB via `tools/semantic_files.py` (`index_directory`). Query via `semantic_file_search` instead of re-reading raw markdown once skill file grows past context — re-run `index_directory` on folder after new notes to keep current.

## Design Principles
- **Prefer prompt/code/system design over regex routing.** Regex fine for one bounded precise thing; use good prompts, clean code, clean APIs for intent classification/routing/parsing.

## Key Files
| File | Purpose |
|------|---------|
| `chat_ui.py` | FastAPI server (~70 routes), UI in `templates/`+`static/` |
| `harness.py` | Main orchestrator — intent dispatch, tools, history |
| `clients/router.py` | Intent classifiers (`classify()`, `classify_telegram()`, ...) |
| `clients/ollama_client.py` | Local Ollama chat client — tool loop, streaming |
| `clients/cloud_client.py` | OpenRouter client — DeepSeek primary, Claude Haiku fallback |
| `tools/registry.py` | Tool schemas + EXECUTORS map (~260 tools) |
| `tools/orchestrator_tools.py` | `ask_*` subagent tools orchestrator calls |
| `tools/websearch.py` | Web search pipeline (guard→rewrite→search→rerank→summarize) |
| `tools/connector_{doordash,uber}.py` | Service connectors (BrowserOS-driven) |
| `agents/local_agent_*.py` | LangGraph local agent — graph, nodes, tool filter |
| `tools/semantic_files.py` | LanceDB meaning-based search (`semantic_file_search`, `index_directory`) — nomic-embed-text via Ollama |
| `tools/fulltext_search.py` | Tantivy BM25 keyword search (`fulltext_search`, `index_directory_fts`) — chunked 1500/200 like semantic_files, no embedding calls |
| `store/conversation.py` | Per-chat sliding window history |
| `skills_hub.py` + `skills_data/*.py` | ~200 interactive skills, native + auto-discovered external |
| `core.py` | Runs chat_ui+telegram_bot+email_watch+task_worker as threads in **one** process. Restart after any imported-code edit: `launchctl kickstart -k gui/$(id -u)/com.clixen.core` |
| `messaging_supervisor.sh` | Supervises Telegram + WhatsApp bridge/bot, one process group |
| `jobs/worker.py` | Polls `job_queue` (one-shot) + `workflow_store` (scheduled automations) |
| `tools/verse_agent.py` | Clixen's agent identity in the aiverse "Verse" sim — `ask_verse_agent`/`check_verse_replies`/`observe_verse` |
| `jobs/verse_ask_poll_job.py` | Background poll for a Verse peer reply, relevance-gated before accepting |
| `tools/preview_watch.py` | Tracks which page of which PDF is open in macOS Preview.app, extracts that page's text — `get_preview_current_page` tool |

## Models
Cloud-first (2026-07): main agent default cloud via OpenRouter — local gemma4 unreliable on browser/DOM-probing tasks (CSS-selector loops, hallucinated content). Correction (2026-08-04): gemma4:12b-mlx verified live chain 10 real tool calls clean, incl. implicit dependency-graph inference, decoy-tool rejection, mid-chain error recovery — "2-3 call ceiling" was infra timeout/recursion-cap artifact, not model limit. Detail: `docs/agents/models.md`.

| Model | Where | Role |
|-------|-------|------|
| `deepseek/deepseek-v4-flash` | Cloud | **Default** — chat + agentic tool use |
| `openrouter/anthropic/claude-haiku-4.5` | Cloud | Fallback on error / 3 consecutive tool errors |
| `openrouter/google/gemini-3.1-flash-lite` | Cloud | Primary vision (`harness.py` routes `vision` intent here via `CLOUD_VISION_MODEL`) |
| `gemma4:12b` (GGUF) | Local | OCR/vision-capable, intent classifier, manual override, IDE-mode fallback — replaced `-mlx` tag 2026-08-08, mlx never received image bytes (Ollama runtime bug) |
| `qwen3:8b` | Local | Query rewriting, history compaction — replaced `qwen3.5:4b` 2026-08-19, that tag never actually pulled |
| `tools/local_vision.py` | Local | Replaces qwen3-vl for most screenshot analysis tasks |
| `nomic-embed-text` | Local | Embeddings (`tools/semantic_files.py`, `store/knowledge_base.py`) |

`OPENROUTER_API_KEY` in `tools-harness/.env`. `cost_guard.py` enforces daily token budget. Full detail: `docs/agents/models.md`.

**Dead-provider breaker** (`cloud_client.py:287-342`): 402/auth failure → provider prefix marked dead in-memory 24h, falls through chain to `OPENAI_FALLBACK_MODEL` (`gpt-4o-mini`). Resets on `core.py` restart. `core_stderr.log` `marked ... as dead` = credit top-up needed, not bug.

## Routing
`router.py` classifiers pick **intent**, not model — every branch defaults cloud except `ocr` (local only). `harness.py` post-classify block re-overrides `routed_model` for some intents (cloud-primary, local fallback). Branch order load-bearing. Detail: `docs/agents/models.md`.

## UI Modes (chat_ui.py)
**Auto**: router picks model. **Dev**: `chat_id` prefix `ide_` → local model + full fs/git/shell tools. **Models**: manual picker.

## Tool Calling
Native Ollama tool calling on capable local models (~7B+ minimum reliable agentic chains). `ollama_client.py` + local-agent both inject "pipeline hints" to force multi-step chaining where needed. See `docs/agents/local-agent-tools.md`.

## Web Search
Single pipeline, no LangGraph, zero API keys needed for base path: `guard → rewrite → search(SearXNG+DDG, parallel) → rerank → summarize(gemma4) → finalize`. Chinese queries short-circuit to `agent_reach.py` (Bilibili + Exa). Full architecture + bug history: `docs/agents/web-search.md`.

## Connectors (BrowserOS)
`tools/connector_{doordash,uber,sofascore}.py` drive real logged-in BrowserOS session; `"browser"`-tagged, reachable from web/Telegram, not LangGraph local-agent (known gap). Detail: `docs/agents/browser-automation.md`.
Ringback (SIP phone calls / `call_my_phone`) removed 2026-09-03 — image had been pruned from docker, feature deleted rather than rebuilt.

## Telegram + WhatsApp (single supervised job)
One launchd job (`com.clixen.messaging.plist`) → `messaging_supervisor.sh` supervises `telegram_bot.py`+`whatsapp_bot.py`+`whatsapp_bridge.js` as one process group. `KeepAlive=true` — unload before manual kill. Both → `classify_telegram()` → `harness.run()`. Kokoro TTS for spoken replies.

## Verse Integration (aiverse)
Clixen holds its own agent identity (owner/token in `tools-harness/.env`, `VERSE_GATEWAY_URL`/`VERSE_AGENT_TOKEN`) in a separate local project (`~/Developer/aiverse`), a multi-agent social sim. Three tools, all in `tools/verse_agent.py`:
- `ask_verse_agent(query)` — posts as Clixen, returns immediately (never blocks on a reply — Verse peers tick on their own 30-150s cadence). Queues `verse_ask_poll_job.py` in the background.
- `check_verse_replies()` — read-only, drains pending reply notifications. Use for "did anyone respond."
- `observe_verse(limit)` — read-only, raw recent room activity regardless of whether it's a reply. Use for "what's happening in the Verse" — **must** be the tool for this; the orchestrator has separately called `query_subagent_findings` (Clixen-internal jobs, unrelated) and fabricated a "Verse summary" from that when this tool didn't exist yet.

Gotchas, all confirmed live (2026-09-02):
- New Verse tools only reach the model if added to `harness.py`'s `orchestrator_tools` list — that's a hand-curated list of schema constants, separate from `tools/registry.py`'s `ALL_TOOLS`/`EXECUTORS`. Registering a tool there is necessary but not sufficient.
- Verse peers (native/subject harnesses) don't reliably react to an injected message — often just continue whatever they were already discussing. A naive "any new message after mine = the reply" check false-positives on that. `verse_ask_poll_job._is_relevant()` gates on a cheap LLM judge (fails safe to `False`, never hangs the job) before accepting a message as a real answer.
- Persona only reaches Clixen's *owner-facing* replies via `ORCHESTRATOR_SYSTEM_PROMPT` by default — the actual `query` text posted *into* Verse defaults to flat/generic unless the tool's own description explicitly asks for first-person voice (see `ask_verse_agent`'s schema). Without that, messages read like a corporate assistant, not a participant.
- Clixen has no memory of its own Verse relationship across calls — no fact like "already asked X" or "peer Y is active" persists. Each `ask_verse_agent` call is stateless beyond the current chat turn.

## Preview.app PDF Page Watch (2026-09-14)
Clixen can see what page of what PDF the user has open in macOS Preview.app — `get_preview_current_page` tool (direct in `harness.py` orchestrator_tools, no subagent hop, same tier as `get_current_time`). Not browser-based, not vision/screenshot — text extraction from the actual PDF file.

- **Poller** (`tools/preview_watch.py:run_preview_watch_daemon`, thread in `core.py`'s `_TARGETS["preview_watch"]`, 1.5s interval): reads Preview's front-most window title via **System Events**, not Preview's own AppleScript dictionary — `tell application "Preview" to get name of window 1` returns just the filename with no page info; `tell application "System Events" to tell process "Preview" to get name of window 1` returns `"<file> – Page N of M"`. Doc path via `tell application "Preview" to get path of document 1` (this one *is* app-level, works fine).
- State (`~/.config/g4l/data/preview_watch_state.json`) is cross-process — the poller thread lives in `core.py`, the tool call reaches it from a separate MCP server process, so state must be file-based, not an in-memory dict.
- Page text reuses `tools/structured.py:read_pdf` (poppler `pdftotext`/`pdfinfo` + OCR fallback) — not PyMuPDF, matches the rest of the codebase's PDF handling.
- Per-page TTL cache (`~/.config/g4l/data/preview_page_cache.json`, 10 min TTL, capped 50 entries): flipping back to a recently-seen page skips re-running `pdftotext`. A failed extraction is never cached (retries next tick) — matters because a fresh core.py restart can catch an iCloud-synced PDF still mid-download (see PATH gotcha below).
- Fence: front-most Preview window only, single document. Other PDF apps (Acrobat, Skim) and multi-window not covered.

Gotcha, confirmed live (2026-09-14): `com.clixen.core.plist`'s `PATH` was missing `/opt/homebrew/bin` (Homebrew's `pdfinfo`/`pdftotext` on Apple Silicon) — under launchd's stripped env, `shutil.which("pdfinfo")` silently returned `None`, `_pdf_page_count` fell back to its `return 1` default, and every extraction failed with "no extractable text in selected pages 1-1" even though the same code worked fine from an interactive shell. This affects *any* subprocess call under core.py, not just this feature — fixed by adding `/opt/homebrew/bin` to the plist's `PATH` (not repo-tracked, lives only at `~/Library/LaunchAgents/com.clixen.core.plist`; a plist edit needs `launchctl unload`+`load`, `kickstart -k` alone won't pick up env changes).

## Tauri Desktop App (next phase)
Not yet built. See `.claude/skills/tauri-migration-plan/SKILL.md`.

## Automations & Task Worker
`jobs/worker.py` (in `core.py`'s `task_worker` thread) polls every 10s: one-shot `job_queue` + scheduled `workflow_store`. 14 builtin automations seeded idempotently on startup. User/LLM-created automations share `automation_id="user.automation"` → generic handler reads `action_type`/`config` (telegram/notification/webhook/email/tool_call/imessage/workflow-with-branching). Full detail + bug history: `docs/agents/automations.md`.

## Agent Reliability Infra
Landed after live failure where subagent silently skipped source, confidently answered "nothing found." Now: reliable prefix-anchored error detection, tool-schema guidance over prompt keyword-branches, fan-out routing for commitment questions, structured subagent trace envelopes (`[subagent ... status=ok/degraded]`), verify-on-absence retry, nightly golden-query regression suite. Full detail: `docs/agents/orchestrator.md`.

## Skills Hub
`skills_hub.py` + `skills_data/*.py` — interactive chat-triggered skills (distinct from background automations). Native skills + auto-discovered external `SKILL.md` files (`~/.claude/skills/`, `~/.agents/skills/`). Single-tool skills skip LLM via `direct_tool` fast path. Full detail: `docs/agents/skills-hub.md`.

## Known Issues / Watch Out
- **SearXNG container**: must be `docker run -p 8888:8080` (image listens 8080 internally, NOT 8888) — `-p 8888:8888` leaves nothing behind mapped port (connection reset every request, easy mistake for container down). Also needs `search: {formats: [html, json]}` appended to `/etc/searxng/settings.yml` (current image's default template has no `formats:` line to sed-replace). Full recipe in `tools/searxng_search.py` docstring. If ever gone after `docker system prune`, all web search silently falls through to Exa/Tavily/Brave/DDG, which can also all be dead at once (rate-limited/expired keys) — check `docker ps | grep searxng` first.
- LanceDB file lock: never run test scripts against harness while running
- Messaging job `KeepAlive=true` — `launchctl unload` before manual kill
- New Google API scope needs `python tools/google_auth.py --auth`
- `automation` intent must check before `_TASKS_RE` in `router.py`
- **Secrets**: `tools-harness/.env` only, never project root `.env`
- **onnxruntime CoreML unstable on Apple Silicon** — Kokoro TTS defaults `CPUExecutionProvider`; don't revert without load-testing under concurrency
- **`fut.result(timeout=X)` doesn't kill the thread, even with explicit `ex.shutdown(wait=False)`** — it only stops *waiting*; the submitted thread runs to its own natural completion regardless, unaware the job was already marked failed. Confirmed live 2026-09-02: a timed-out `verse_ask_poll` job kept polling/judging for its full ~15 min, wrote a second duplicate notification, and blocked `jobs/worker.py`'s single-threaded dispatch loop from claiming other queued jobs the whole time. Fixed generically in `worker.py`'s `_execute_job`: a `threading.Event` is created per job and passed as `cancel_event` kwarg to `run_as_job` only if the module's signature declares that param (via `inspect.signature`, so `agent_message`/`morning_briefing`/`tech_brief` are untouched); the module must itself check it (e.g. `cancel_event.wait(poll_seconds)` instead of `time.sleep`) to actually stop early. Any new long-running `job_queue` task should do the same.
- **Two separate OpenAI keys**: `OPENAI_API_KEY` (used only by `store/knowledge_base.py`'s embedder) vs `OPENAI_REAL_API_KEY` (used by `clients/cloud_client.py`'s `"openai/"` model prefix, the last-resort cloud tier). They can silently drift — one can be dead while the other works, and every "openai/gpt-4o-mini failed" log line is ambiguous about which key was actually used. Check both when diagnosing a 401 on this tier.

## Local Agent (LangGraph) — Form Filling & Coding
Task-scoped toolset (`document`/`code`/`full`) in `agents/local_agent_tools.py`. Coding mode has diff+undo on edits, git status/diff visibility, confirm-before-execute for destructive shell commands. Form workflow: `detect_form_fields → fill_form → detect_form_fields(filled) → confirm`. Skill recipes from `skills_hub.py` (`match_skill()`) injected into system prompt (`[SKILL: name — desc]` block) to give weak local models known-good plan instead of free-planning. Full detail, gotchas, testing: `docs/agents/local-agent-tools.md`.

### Two local agent loops — not redundant, don't merge
- **`agents/local_agent_graph.py` (`run_local_agent`)** — LangGraph, structured plan→step→tool nodes, skill injection, diff+undo, confirm-before-destructive-shell. **This is the one shipping in the Tauri desktop app** and the one to target for eval work going forward (`skill_eval.py`).
- **`harness.py` (`run(force_local_agent=True)`)** — legacy flat tool-calling loop via `ollama_client.chat()`, no skill injection, no safety gates. Kept only because existing callers (telegram doc-intent synthesis, IDE mode) already route through it deliberately — telegram doc intent avoids LangGraph loop on purpose (old loop hung ~80s/round; doc intent redesigned deterministic gather→synthesize, no agent loop at all). Don't build new eval infra or features against this path.

<!-- forge-learnings:start -->
## Learnings (auto-maintained by /um — human edits go ABOVE this block)
- `src/g4l/` frozen phase-1 prototype; only `core/models.py` + `core/utils.py` imported by production `chat_ui.py`. `tools-harness/` is real runtime.
- `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` set daemon-wide (`launchctl setenv`, needs killing `ollama serve`+Ollama.app parent to take effect) — verified live, second model load fully evicts first. Helped prefill (84 tok/s), decode flat ~8.8 tok/s (12B Q4 decode bandwidth-bound on M4, not server-flag fix).
- Telegram document intent (`_run_doc_agent`): deterministic gather→one `chat(tools=[])` synthesis→convert; NO agent loop (old loop hung ~80s/round). `_content_query` strips format words before web search; converters return error STRINGS not raises (verify file on disk).
- `local_agent_nodes.py` ollama calls use `ollama.Client(timeout=120)` — unbounded before, caused silent hangs on long prompts.
- Doc formats: pdf/docx/xlsx work (openpyxl installed); pptx needs python-pptx.
- Two Ollama installs exist: `homebrew.mxcl.ollama.plist` dead (port-conflicted). Real one is Ollama.app's Electron-spawned `ollama serve` — env vars only update via `launchctl setenv` + killing both `ollama serve` child and parent Electron process, then reopening app. Editing either plist no-op for live daemon.
- Warm-daemon pattern for cold-exec-per-invocation scripts (e.g. `brabble_hook.py`, execs fresh per wake-word): tiny stdlib `http.server` daemon holding one singleton, wired as `core.py`-supervised thread. Used for `kokoro_daemon.py` (:9237) and `voiceprint_daemon.py` (:9238) — same shape for any cold-import-heavy dependency (Kokoro TTS, Resemblyzer voiceprint).
- `ThreadPoolExecutor` as `with ... as ex:` defeats `.result(timeout=X)` — `__exit__`'s `shutdown(wait=True)` blocks until thread finishes anyway. Use `ex = ThreadPoolExecutor(...)` + explicit `ex.shutdown(wait=False)` when real timeout matters.
- `tools/websearch.py`: Tavily/rewrite calls previously had no enforced timeout (Tavily SDK default 60s, `_rewrite_query`'s `timeout_s` declared but never applied) — real outliers hit 90s+. Now: 10s Tavily cap, 15s cap on SearXNG/DDG/Brave fallback tier, `_rewrite_query` defaults to cloud (DeepSeek) not local `qwen3.5:4b`.
- Conversation fold (`store/conversation.py`): labeling transcript `USER:`/`ASSISTANT:` makes summarizer model hallucinate conversational reply instead of extracting facts (chat-shaped input triggers "continue this chat" prior stronger than system prompt). Use neutral tags (`[A]`/`[B]`) + explicit "inert data, do not respond" framing.
- Real barge-in for cold-exec voice hook needs 4 steps together: SIGKILL previous hook process by PID (from lock file) → `pkill` orphaned audio subprocesses (outlive killed parent) → hit `/chat/abort` server-side → force-clear lock file. Killing only audio leaves old process holding lock.
- Word (.docx) review comments live in separate zip entry (`word/comments.xml`), not exposed by `python-docx` or `anydoc` (`anydoc.Document.notes` = footnotes, not comments — confirmed empirically, 0 notes on doc with 269 real comments). `docx2python` (MIT) exposes them cleanly via `.comments` → `(anchor, author, date, text)` tuples. Two independent doc readers needed fix: `office_tools.docx_to_markdown` (semantic-index path) AND `structured.py`'s hand-rolled `_read_docx` (actual agent-facing `read_document` tool) — don't share code. When appending comments to truncated body, reserve comments' char budget FIRST or `[:max_chars]` silently drops them on any doc where body alone exceeds limit.
- Tantivy (`tools/fulltext_search.py`, BM25 keyword search) vs LanceDB (`tools/semantic_files.py`, meaning-based vector search) complementary, not interchangeable — don't build heuristic router, let tool-schema descriptions do routing (each schema explicitly names other as better fit for opposite case) per regex-routing design principle above. Verified via live no-keyword-overlap query ("sites never set money aside for keeping gear running so it fails silently" vs source text using "PSA plant", "maintenance budget", "servicing agreements"): LanceDB found real conceptual match, Tantivy drifted onto unrelated passages sharing only incidental words. Tantivy's real edge is raw speed (10-800x faster, no embedding HTTP round-trip to Ollama) and literal/exact-term precision — initial claim of tantivy "beating" LanceDB on accuracy wrong, based on keyword-heavy test queries favoring BM25 by construction. Also: Tantivy indexing must chunk (same 1500/200 as LanceDB) or BM25 scores dilute across whole-file blobs, surface wrong section of large doc; schema changes (e.g. adding `chunk_index`, switching `path` field to `tokenizer_name="raw"` for exact-term delete) require wiping on-disk index dir — `tantivy.Index.open()` on old-schema index doesn't auto-migrate.
<!-- forge-learnings:end -->