# Clixen — Project Guide for Claude

## What This Is
Local LLM harness + chat app, on-device (Apple M4, 24 GB unified memory).
- **Web UI**: http://localhost:9234 — vanilla HTML/JS, FastAPI, SSE streaming
- **Telegram + WhatsApp bots**: one launchd job supervising both
- **Voice**: Brabble (wake-word) + ringback (real SIP phone calls)
- **Repo vs package name**: repo dir is `clixen`; launchd label prefix renamed to `com.clixen.*` (2026-08-03)
- **`AGENTS.md`**: deeper agent-dev reference — tool inventory, specialist dispatch, browser automation, connectors, voice. Points into `docs/agents/*.md` for full depth.
- **Standalone script runs**: use `~/Developer/clixen/.venv/bin/python`, not system python — it has `mcp` and all deps; system python is too old/missing packages for some modules.

## Design Principles
- **Prefer prompt/code/system design over regex routing.** Regex is fine for one bounded, precise thing; use good prompts, clean code, and clean APIs for intent classification/routing/parsing.

## Key Files
| File | Purpose |
|------|---------|
| `chat_ui.py` | FastAPI server (~70 routes), UI in `templates/`+`static/` |
| `harness.py` | Main orchestrator — intent dispatch, tools, history |
| `clients/router.py` | Intent classifiers (`classify()`, `classify_telegram()`, ...) |
| `clients/ollama_client.py` | Local Ollama chat client — tool loop, streaming |
| `clients/cloud_client.py` | OpenRouter client — DeepSeek primary, Claude Haiku fallback |
| `tools/registry.py` | Tool schemas + EXECUTORS map (~260 tools) |
| `tools/orchestrator_tools.py` | `ask_*` subagent tools the orchestrator calls |
| `tools/websearch.py` | Web search pipeline (guard→rewrite→search→rerank→summarize) |
| `tools/connector_{doordash,uber,ringback}.py` | Service connectors (BrowserOS-driven, or ringback's real SIP call) |
| `agents/local_agent_*.py` | LangGraph local agent — graph, nodes, tool filter |
| `store/conversation.py` | Per-chat sliding window history |
| `skills_hub.py` + `skills_data/*.py` | ~200 interactive skills, native + auto-discovered external |
| `core.py` | Runs chat_ui+telegram_bot+email_watch+task_worker as threads in **one** process. Restart after any imported-code edit: `launchctl kickstart -k gui/$(id -u)/com.clixen.core` |
| `messaging_supervisor.sh` | Supervises Telegram + WhatsApp bridge/bot, one process group |
| `jobs/worker.py` | Polls `job_queue` (one-shot) + `workflow_store` (scheduled automations) |

## Models
Cloud-first (2026-07): main agent defaults to cloud via OpenRouter — local gemma4 was unreliable past 2-3 chained tool calls.

| Model | Where | Role |
|-------|-------|------|
| `deepseek/deepseek-v4-flash` | Cloud | **Default** — chat + agentic tool use |
| `openrouter/anthropic/claude-haiku-4.5` | Cloud | Fallback on error / 3 consecutive tool errors |
| `gemma4:12b-mlx` | Local | OCR/vision (only multimodal model), intent classifier, manual override |
| `qwen3.5:4b` | Local | Query rewriting, history compaction |
| `qwen3-vl:8b`/`4b` | Local | Vision (OCR, screenshots) |
| `bge-reranker-v2-m3` / `nomic-embed-text` | Local | Search reranker / embeddings |

`OPENROUTER_API_KEY` lives in `tools-harness/.env` (not project root). `clients/cost_guard.py` enforces a daily token budget (`CLOUD_DAILY_TOKEN_BUDGET`). See `docs/agents/models.md` for routing internals, thinking-mode gotchas, warmup/KV-cache detail.

## Routing
`router.py`'s classifiers pick an **intent**, not a model — every branch defaults to cloud except `ocr` (stays local, only multimodal model). `harness.py`'s post-classify block is what actually re-overrides `routed_model` for several intents (browser/transit/vision/etc, cloud-primary with automatic local fallback). Branch order in the classifiers is load-bearing — reordering is a behavior change. Full detail: `docs/agents/models.md`.

## UI Modes (chat_ui.py)
**Auto**: router picks model. **Dev**: `chat_id` prefix `ide_` → local model + full fs/git/shell tools. **Models**: manual picker.

## Tool Calling
Native Ollama tool calling on capable local models (~7B+ minimum for reliable agentic chains). `ollama_client.py` and the local-agent both inject "pipeline hints" to force multi-step chaining where needed. See `docs/agents/local-agent-tools.md`.

## Web Search
Single pipeline, no LangGraph, zero API keys required for the base path: `guard → rewrite → search(SearXNG+DDG, parallel) → rerank → summarize(gemma4) → finalize`. Chinese queries short-circuit to `agent_reach.py` (Bilibili + Exa). Full architecture + bug history: `docs/agents/web-search.md`.

## Connectors (BrowserOS + Ringback)
`tools/connector_{doordash,uber,sofascore}.py` drive a real logged-in BrowserOS session (no scraping/API keys); `"browser"`-tagged, reachable from web/Telegram but **not** the LangGraph local-agent (toolset gap, by design/not yet closed). `tools/connector_ringback.py`'s `call_my_phone()` places a real SIP call, gated by a 900s/15min cooldown (`ringback/.last_call_ts`, flock-guarded, restore-on-fail) — tool description tells the model to quote the exact cooldown remaining-seconds verbatim rather than paraphrase it. Full detail + bug history: `docs/agents/browser-automation.md`.

## Telegram + WhatsApp (single supervised job)
One launchd job (`com.clixen.messaging.plist`) runs `messaging_supervisor.sh`, which supervises `telegram_bot.py` + `whatsapp_bot.py` + `whatsapp_bridge.js` as one process group. `KeepAlive=true` — unload before manual kill. Both routers → `classify_telegram()` → `harness.run()`. Telegram/WhatsApp use Kokoro TTS for spoken replies.

## Tauri Desktop App (next phase)
Not yet built. See `.claude/skills/tauri-migration-plan/SKILL.md`.

## Automations & Task Worker
`jobs/worker.py` (in `core.py`'s `task_worker` thread) polls every 10s: one-shot `job_queue` and scheduled `workflow_store`. 7 builtin automations seeded idempotently on startup. User/LLM-created automations share `automation_id="user.automation"` → generic handler reads `action_type`/`config` (telegram/notification/webhook/email/tool_call/imessage/workflow-with-branching). Full detail + bug history: `docs/agents/automations.md`.

## Agent Reliability Infra
Landed after a live failure where a subagent silently skipped a source and confidently answered "nothing found." Now: reliable prefix-anchored error detection, tool-schema guidance over prompt keyword-branches, fan-out routing for commitment questions, structured subagent trace envelopes (`[subagent ... status=ok/degraded]`), a verify-on-absence retry, and a nightly golden-query regression suite. Full detail: `docs/agents/orchestrator.md`.

## Skills Hub
`skills_hub.py` + `skills_data/*.py` — interactive chat-triggered skills (distinct from background automations). Native skills + auto-discovered external `SKILL.md` files (`~/.claude/skills/`, `~/.agents/skills/`). Single-tool skills skip the LLM via a `direct_tool` fast path. Full detail: `docs/agents/skills-hub.md`.

## Known Issues / Watch Out
- LanceDB file lock: never run test scripts against harness while it's running
- Messaging job `KeepAlive=true` — `launchctl unload` before manual kill
- New Google API scope needs `python tools/google_auth.py --auth`
- `automation` intent must be checked before `_TASKS_RE` in `router.py`
- **Secrets**: `tools-harness/.env` only, never project root `.env`
- **onnxruntime CoreML unstable on Apple Silicon** — Kokoro TTS defaults `CPUExecutionProvider`; don't revert without load-testing under concurrency

## Local Agent (LangGraph) — Form Filling & Coding
Task-scoped toolset (`document`/`code`/`full`) in `agents/local_agent_tools.py`. Coding mode has diff+undo on edits, git status/diff visibility, and confirm-before-execute for destructive shell commands. Form workflow: `detect_form_fields → fill_form → detect_form_fields(filled) → confirm`. Full detail, gotchas, testing: `docs/agents/local-agent-tools.md`.

<!-- forge-learnings:start -->
## Learnings (auto-maintained by /um — human edits go ABOVE this block)
- Repo dir renamed gemma4llama→clixen; package name, README, launchd label prefix `com.clixen.*` (renamed 2026-08-03). Watch for hardcoded `/gemma4llama` paths (`tools/path_policy.py` WORKSPACE_ROOT must derive from `__file__`).
- Messaging is ONE launchd job `com.clixen.messaging.plist` → `messaging_supervisor.sh` (telegram_bot + whatsapp_bot:9236 + whatsapp_bridge:9235). Logs: `tools-harness/messaging_std{err,out}.log`. Restart via `launchctl unload/load` (KeepAlive=true).
- `src/g4l/` is a frozen phase-1 prototype; only `core/models.py` + `core/utils.py` are imported by production `chat_ui.py`. `tools-harness/` is the real runtime.
- Router `classify()`/`classify_telegram()`: every branch returns cloud (`CLOUD_MODEL`) except `ocr`, which stays `gemma4:12b-mlx` (only multimodal model) — flipped cloud-first 2026-07, this line was stale (used to say every branch returns gemma4:12b-mlx). `harness.py`'s post-classify if/elif still re-overrides some intents back to local, see CLAUDE.md → Routing.
- Telegram document intent (`_run_doc_agent`): deterministic gather→one `chat(tools=[])` synthesis→convert; NO agent loop (old loop hung ~80s/round). `_content_query` strips format words before web search; converters return error STRINGS not raises (verify file on disk).
- `local_agent_nodes.py` ollama calls use `ollama.Client(timeout=120)` — unbounded before, caused silent hangs on long prompts.
- Doc formats: pdf/docx/xlsx work (openpyxl installed); pptx needs python-pptx.
- Two Ollama installs exist: `homebrew.mxcl.ollama.plist` is dead (port-conflicted). The real one is Ollama.app's Electron-spawned `ollama serve` — its env vars only update via `launchctl setenv` + killing both the `ollama serve` child and parent Electron process, then reopening the app. Editing either plist is a no-op for the live daemon.
- Warm-daemon pattern for cold-exec-per-invocation scripts (e.g. `brabble_hook.py`, which execs fresh per wake-word): a tiny stdlib `http.server` daemon holding one singleton, wired as a `core.py`-supervised thread. Used for `kokoro_daemon.py` (:9237) and `voiceprint_daemon.py` (:9238) — same shape for any cold-import-heavy dependency (Kokoro TTS, Resemblyzer voiceprint).
- `ThreadPoolExecutor` as `with ... as ex:` defeats `.result(timeout=X)` — `__exit__`'s `shutdown(wait=True)` blocks until the thread finishes anyway. Use `ex = ThreadPoolExecutor(...)` + explicit `ex.shutdown(wait=False)` when a real timeout matters.
- `tools/websearch.py`: Tavily/rewrite calls previously had no enforced timeout (Tavily SDK default 60s, `_rewrite_query`'s `timeout_s` was declared but never applied) — real outliers hit 90s+. Now: 10s Tavily cap, 15s cap on the SearXNG/DDG/Brave fallback tier, and `_rewrite_query` defaults to cloud (DeepSeek) not local `qwen3.5:4b`.
- Conversation fold (`store/conversation.py`): labeling the transcript `USER:`/`ASSISTANT:` makes the summarizer model hallucinate a conversational reply instead of extracting facts (chat-shaped input triggers a "continue this chat" prior stronger than the system prompt). Use neutral tags (`[A]`/`[B]`) + explicit "inert data, do not respond" framing.
- Real barge-in for a cold-exec voice hook needs 4 steps together: SIGKILL the previous hook process by PID (from the lock file) → `pkill` orphaned audio subprocesses (they outlive a killed parent) → hit `/chat/abort` server-side → force-clear the lock file. Killing only the audio leaves the old process holding the lock.
<!-- forge-learnings:end -->
