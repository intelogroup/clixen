# Clixen — Project Guide for Claude

## What This Is
Local LLM harness + chat app, on-device (Apple M4, 24 GB unified memory).
- **Web UI**: http://localhost:9234 — vanilla HTML/JS, FastAPI, SSE streaming
- **Telegram + WhatsApp bots**: one launchd job supervising both
- **Voice**: Brabble (wake-word) + ringback (real SIP phone calls)
- **Repo vs package name**: repo dir is `clixen`; launchd label prefix renamed to `com.clixen.*` (2026-08-03)
- **`AGENTS.md`**: deeper agent-dev reference — tool inventory, specialist dispatch, browser automation, connectors, voice. Points into `docs/agents/*.md` for full depth.
- **Standalone script runs**: use `~/Developer/clixen/.venv/bin/python`, not system python — it has `mcp` and all deps; system python is too old/missing packages for some modules.
- **Type check**: `npm run typecheck` (`tsc --noEmit`) — run after any `.ts`/`.tsx` edit, no build step needed.

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
| `tools/semantic_files.py` | LanceDB meaning-based search (`semantic_file_search`, `index_directory`) — nomic-embed-text via Ollama |
| `tools/fulltext_search.py` | Tantivy BM25 keyword search (`fulltext_search`, `index_directory_fts`) — chunked 1500/200 like semantic_files, no embedding calls |
| `store/conversation.py` | Per-chat sliding window history |
| `skills_hub.py` + `skills_data/*.py` | ~200 interactive skills, native + auto-discovered external |
| `core.py` | Runs chat_ui+telegram_bot+email_watch+task_worker as threads in **one** process. Restart after any imported-code edit: `launchctl kickstart -k gui/$(id -u)/com.clixen.core` |
| `messaging_supervisor.sh` | Supervises Telegram + WhatsApp bridge/bot, one process group |
| `jobs/worker.py` | Polls `job_queue` (one-shot) + `workflow_store` (scheduled automations) |

## Models
Cloud-first (2026-07): main agent defaults to cloud via OpenRouter — local gemma4 was unreliable on browser/DOM-probing tasks (CSS-selector loops, hallucinated content). Correction (2026-08-04): gemma4:12b-mlx verified live to chain 10 real tool calls cleanly, incl. implicit dependency-graph inference, decoy-tool rejection, and mid-chain error recovery — the "2-3 call ceiling" was an infra timeout/recursion-cap artifact, not a model limit. Detail: `docs/agents/models.md`.

| Model | Where | Role |
|-------|-------|------|
| `deepseek/deepseek-v4-flash` | Cloud | **Default** — chat + agentic tool use |
| `openrouter/anthropic/claude-haiku-4.5` | Cloud | Fallback on error / 3 consecutive tool errors |
| `openrouter/google/gemini-3.1-flash-lite` | Cloud | Primary vision (`harness.py` routes `vision` intent here via `CLOUD_VISION_MODEL`) |
| `gemma4:12b` (GGUF) | Local | OCR/vision-capable, intent classifier, manual override, IDE-mode fallback — replaced `-mlx` tag 2026-08-08, mlx never received image bytes (Ollama runtime bug) |
| `qwen3.5:4b` | Local | Query rewriting, history compaction |
| `tools/local_vision.py` | Local | Replaces qwen3-vl for most screenshot analysis tasks |
| `nomic-embed-text` | Local | Embeddings (`tools/semantic_files.py`, `store/knowledge_base.py`) |

`OPENROUTER_API_KEY` in `tools-harness/.env`. `cost_guard.py` enforces daily token budget. Full detail: `docs/agents/models.md`.

**Dead-provider breaker** (`cloud_client.py:287-342`): 402/auth failure → provider prefix marked dead in-memory 24h, falls through chain to `OPENAI_FALLBACK_MODEL` (`gpt-4o-mini`). Resets on `core.py` restart. `core_stderr.log` `marked ... as dead` = credit top-up needed, not a bug.

## Routing
`router.py` classifiers pick **intent**, not model — every branch defaults cloud except `ocr` (local only). `harness.py` post-classify block re-overrides `routed_model` for some intents (cloud-primary, local fallback). Branch order is load-bearing. Detail: `docs/agents/models.md`.

## UI Modes (chat_ui.py)
**Auto**: router picks model. **Dev**: `chat_id` prefix `ide_` → local model + full fs/git/shell tools. **Models**: manual picker.

## Tool Calling
Native Ollama tool calling on capable local models (~7B+ minimum for reliable agentic chains). `ollama_client.py` and the local-agent both inject "pipeline hints" to force multi-step chaining where needed. See `docs/agents/local-agent-tools.md`.

## Web Search
Single pipeline, no LangGraph, zero API keys required for the base path: `guard → rewrite → search(SearXNG+DDG, parallel) → rerank → summarize(gemma4) → finalize`. Chinese queries short-circuit to `agent_reach.py` (Bilibili + Exa). Full architecture + bug history: `docs/agents/web-search.md`.

## Connectors (BrowserOS + Ringback)
`tools/connector_{doordash,uber,sofascore}.py` drive real logged-in BrowserOS session; `"browser"`-tagged, reachable from web/Telegram, not LangGraph local-agent (known gap). `connector_ringback.py`'s `call_my_phone()` places real SIP call, 900s cooldown (`ringback/.last_call_ts`, flock-guarded) — quote remaining-seconds verbatim, don't paraphrase. Detail: `docs/agents/browser-automation.md`.

## Telegram + WhatsApp (single supervised job)
One launchd job (`com.clixen.messaging.plist`) → `messaging_supervisor.sh` supervises `telegram_bot.py`+`whatsapp_bot.py`+`whatsapp_bridge.js` as one process group. `KeepAlive=true` — unload before manual kill. Both → `classify_telegram()` → `harness.run()`. Kokoro TTS for spoken replies.

## Tauri Desktop App (next phase)
Not yet built. See `.claude/skills/tauri-migration-plan/SKILL.md`.

## Automations & Task Worker
`jobs/worker.py` (in `core.py`'s `task_worker` thread) polls every 10s: one-shot `job_queue` and scheduled `workflow_store`. 14 builtin automations seeded idempotently on startup. User/LLM-created automations share `automation_id="user.automation"` → generic handler reads `action_type`/`config` (telegram/notification/webhook/email/tool_call/imessage/workflow-with-branching). Full detail + bug history: `docs/agents/automations.md`.

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
Task-scoped toolset (`document`/`code`/`full`) in `agents/local_agent_tools.py`. Coding mode has diff+undo on edits, git status/diff visibility, and confirm-before-execute for destructive shell commands. Form workflow: `detect_form_fields → fill_form → detect_form_fields(filled) → confirm`. Skill recipes from `skills_hub.py` (`match_skill()`) are injected into the system prompt (`[SKILL: name — desc]` block) to give weak local models a known-good plan instead of free-planning. Full detail, gotchas, testing: `docs/agents/local-agent-tools.md`.

### Two local agent loops — not redundant, don't merge
- **`agents/local_agent_graph.py` (`run_local_agent`)** — LangGraph, structured plan→step→tool nodes, skill injection, diff+undo, confirm-before-destructive-shell. **This is the one being shipped in the Tauri desktop app** and the one to target for eval work going forward (`skill_eval.py`).
- **`harness.py` (`run(force_local_agent=True)`)** — legacy flat tool-calling loop via `ollama_client.chat()`, no skill injection, no safety gates. Kept only because existing callers (telegram doc-intent synthesis, IDE mode) already route through it deliberately — telegram doc intent in particular avoids the LangGraph loop on purpose (old loop hung ~80s/round; doc intent redesigned to deterministic gather→synthesize, no agent loop at all). Don't build new eval infra or features against this path.

<!-- forge-learnings:start -->
## Learnings (auto-maintained by /um — human edits go ABOVE this block)
- `src/g4l/` is a frozen phase-1 prototype; only `core/models.py` + `core/utils.py` are imported by production `chat_ui.py`. `tools-harness/` is the real runtime.
- `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` set daemon-wide (`launchctl setenv`, needs killing `ollama serve`+Ollama.app parent to take effect) — verified live, second model load fully evicts first. Helped prefill (84 tok/s), decode flat ~8.8 tok/s (12B Q4 decode is bandwidth-bound on M4, not a server-flag fix).
- Telegram document intent (`_run_doc_agent`): deterministic gather→one `chat(tools=[])` synthesis→convert; NO agent loop (old loop hung ~80s/round). `_content_query` strips format words before web search; converters return error STRINGS not raises (verify file on disk).
- `local_agent_nodes.py` ollama calls use `ollama.Client(timeout=120)` — unbounded before, caused silent hangs on long prompts.
- Doc formats: pdf/docx/xlsx work (openpyxl installed); pptx needs python-pptx.
- Two Ollama installs exist: `homebrew.mxcl.ollama.plist` is dead (port-conflicted). The real one is Ollama.app's Electron-spawned `ollama serve` — its env vars only update via `launchctl setenv` + killing both the `ollama serve` child and parent Electron process, then reopening the app. Editing either plist is a no-op for the live daemon.
- Warm-daemon pattern for cold-exec-per-invocation scripts (e.g. `brabble_hook.py`, which execs fresh per wake-word): a tiny stdlib `http.server` daemon holding one singleton, wired as a `core.py`-supervised thread. Used for `kokoro_daemon.py` (:9237) and `voiceprint_daemon.py` (:9238) — same shape for any cold-import-heavy dependency (Kokoro TTS, Resemblyzer voiceprint).
- `ThreadPoolExecutor` as `with ... as ex:` defeats `.result(timeout=X)` — `__exit__`'s `shutdown(wait=True)` blocks until the thread finishes anyway. Use `ex = ThreadPoolExecutor(...)` + explicit `ex.shutdown(wait=False)` when a real timeout matters.
- `tools/websearch.py`: Tavily/rewrite calls previously had no enforced timeout (Tavily SDK default 60s, `_rewrite_query`'s `timeout_s` was declared but never applied) — real outliers hit 90s+. Now: 10s Tavily cap, 15s cap on the SearXNG/DDG/Brave fallback tier, and `_rewrite_query` defaults to cloud (DeepSeek) not local `qwen3.5:4b`.
- Conversation fold (`store/conversation.py`): labeling the transcript `USER:`/`ASSISTANT:` makes the summarizer model hallucinate a conversational reply instead of extracting facts (chat-shaped input triggers a "continue this chat" prior stronger than the system prompt). Use neutral tags (`[A]`/`[B]`) + explicit "inert data, do not respond" framing.
- Real barge-in for a cold-exec voice hook needs 4 steps together: SIGKILL the previous hook process by PID (from the lock file) → `pkill` orphaned audio subprocesses (they outlive a killed parent) → hit `/chat/abort` server-side → force-clear the lock file. Killing only the audio leaves the old process holding the lock.
- Word (.docx) review comments live in a separate zip entry (`word/comments.xml`), not exposed by `python-docx` or `anydoc` (`anydoc.Document.notes` = footnotes, not comments — confirmed empirically, 0 notes on a doc with 269 real comments). `docx2python` (MIT) exposes them cleanly via `.comments` → `(anchor, author, date, text)` tuples. Two independent doc readers needed the fix: `office_tools.docx_to_markdown` (semantic-index path) AND `structured.py`'s hand-rolled `_read_docx` (the actual agent-facing `read_document` tool) — they don't share code. When appending comments to a truncated body, reserve the comments' char budget FIRST or `[:max_chars]` silently drops them on any doc where body alone exceeds the limit.
- Tantivy (`tools/fulltext_search.py`, BM25 keyword search) vs LanceDB (`tools/semantic_files.py`, meaning-based vector search) are complementary, not interchangeable — don't build a heuristic router, let tool-schema descriptions do the routing (each schema explicitly names the other as the better fit for the opposite case) per the regex-routing design principle above. Verified via live no-keyword-overlap query ("sites never set money aside for keeping gear running so it fails silently" vs source text using "PSA plant", "maintenance budget", "servicing agreements"): LanceDB found the real conceptual match, Tantivy drifted onto unrelated passages sharing only incidental words. Tantivy's real edge is raw speed (10-800x faster, no embedding HTTP round-trip to Ollama) and literal/exact-term precision — initial claim of tantivy "beating" LanceDB on accuracy was wrong, based on keyword-heavy test queries that favored BM25 by construction. Also: Tantivy indexing must chunk (same 1500/200 as LanceDB) or BM25 scores dilute across whole-file blobs and surface the wrong section of a large doc; schema changes (e.g. adding `chunk_index`, switching `path` field to `tokenizer_name="raw"` for exact-term delete) require wiping the on-disk index dir — `tantivy.Index.open()` on an old-schema index doesn't auto-migrate.
<!-- forge-learnings:end -->
