# Clixen — Multi-User & Cross-Platform Roadmap

Generated from audit of hardcoded paths, platform assumptions, security leaks, env var
gaps, and startup failures. Goal: runnable on any machine, by any user, with minimal
friction.

---

## 0. Commercial Strategy (Desktop App Packaging)

**Positioning, revised 2026-08-03 after OpenClaw competitive analysis:**

Do NOT compete on "agentic workflows created by asking" or "runs locally" —
OpenClaw (68K stars, non-profit, full-time team, free) already does both:
natural-language → workflow generation is their headline feature, and it
supports 6 model providers including local via Ollama/LM Studio.

**The actual open lane: reliable, zero-required-cloud-spend automation on a
curated workflow set.** OpenClaw's own docs warn local models need the
largest non-quantized build and still degrade reliability — their real usage
skews cloud (Claude CLI primary path), meaning OpenClaw's "free" app hides
cost in the user's cloud API bill. Clixen's pitch: a **narrower, pre-verified
set of workflows (Section 8/9 below) that work reliably fully local, no
cloud spend required** — trade generality for reliability on a smaller
surface, instead of competing on breadth.

**Implication for scope:** don't build a general workflow-generation engine
to match OpenClaw. Build and prove out the curated W1-W10/B1-B10 workflow
templates below running well on local gemma4, and sell that reliability +
zero-cloud-cost guarantee as the differentiator, not the "ask and it builds
a workflow" mechanic itself.

**Verification status (dry-run tested 2026-08-03):**
- `jobs/handlers/daily_digest.py` (W1) currently calls `clients.cloud_client.chat`
  — hardcoded cloud, contradicts the zero-cloud-spend pitch as shipped. Needs
  a code change to call local gemma4 before this claim is honest.
- Tested the same summarization prompt directly against local `gemma4:12b-mlx`
  via `ollama.chat()`: output was correct and on-format, but took **57s** for
  a 3-line digest of 3 short inputs. Fine for a once-daily cron; too slow for
  anything promising near-real-time response (escalation ladder, threshold
  alert) — measure latency per-workflow before selling "fast local" as a
  feature.
- `jobs/handlers/health_check.py` (W5-adjacent) has **no LLM dependency at
  all** — pure Python logic, genuinely zero-cost/zero-latency, already backs
  the "runs local, no cloud, no wait" claim as-is.
- **Done 2026-08-03**: `jobs/handlers/daily_digest.py` converted to
  `clients.ollama_client.chat` (was `clients.cloud_client.chat`). Re-tested
  the exact summarize call end-to-end through the real wrapper: 5.0s on a
  warm model (vs 57s cold-start in the raw-ollama test above) — cold-start
  latency is real and worth surfacing in UX (first digest of the day pays
  the load tax), but output was correct and on-format both times.
- **Done 2026-08-03**: tested a 3-round chained tool-call sequence on
  `gemma4:12b-mlx` (`get_current_time` called for NY, then Tokyo, then
  London, single tool repeated with different params) via
  `ollama_client.chat(tools=[...], max_rounds=6)`. All 3 calls fired
  correctly in order, final synthesis correctly reported all 3 timezones,
  14.2s total. This confirms the narrow "B4 pipeline" shape (fixed sequence,
  one tool, swappable params — daily digest, watch-diff, escalation ladder)
  holds up locally. It does NOT prove out CLAUDE.md's broader warning about
  gemma4 degrading in the main harness loop, which chains many *different*
  tools under open-ended routing — that's a different, harder case and
  still untested.

**Architecture comparison (read 2026-08-03 via GitHub API, no full clone):**

OpenClaw (`github.com/openclaw/openclaw`, TS monorepo, ~90 packages) and
Hermes Agent (`github.com/NousResearch/hermes-agent`, Python fork of the
same lineage, self-evolving) are both far more architecturally mature than
Clixen's automation layer. This kills the "B1-B10 primitives as a
differentiator" plan from Section 9 above:

- OpenClaw's **Task Flow** (`docs/automation/taskflow.md`) is B1/B3/B6
  already shipped: durable SQLite-backed flow records (`flow_runs` table),
  optimistic revision-tracked concurrency (stale writes rejected, not
  clobbered), `approval: required` gates and `condition:` branches on
  individual pipeline steps, survives gateway restarts. Clixen's
  `jobs/worker.py` 10s-poll + `workflow_store` has no restart-safety,
  revision tracking, or approval-gate primitive today.
- OpenClaw's core/plugin split follows one rule: "recurring demand defines
  interfaces" — 3+ plugins wanting the same capability gets promoted to a
  core contract. Deliberately thin core, everything else is a plugin.
- Hermes Agent independently ships `verification_evidence.py` /
  `verify_hooks.py` / `verification_stop.py` — the same idea as Clixen's own
  "Agent Reliability Infra" (verify-on-absence retry, see CLAUDE.md), plus a
  self-improvement loop (`learning_graph.py`/`curator.py`, DSPy+GEPA) that
  rewrites its own skills/prompts over time. Not a gap to fill, already done.

**Revised positioning:** don't compete on workflow-engine sophistication,
generalized agent platform breadth, or self-verification — all three are
matched or exceeded by funded/community projects with far more surface area
than one solo dev can sustain. OpenClaw's own docs recommend cloud-quality
models for reliability and flag local-model degradation; Hermes is
Python/Docker-first, built for an always-on server, not a lightweight
on-device app. Clixen's real remaining edge is being **small, single-user,
Mac-native, and fast to actually use** — no Docker, no gateway process, no
flow-engine SQLite schema to reason about — "runs well on your Mac with
local models, does 4-5 things reliably" rather than "generalized agent
platform." Scope the desktop app around that constraint, not around matching
OpenClaw/Hermes feature-for-feature. Concretely for the primary Tauri
surface (document work): **not an all-around agent** — see §0b for the
locked architecture this implies.

**Target niche (2026-08-04): privacy-bound solo professionals** —
therapists, coaches, consultants, small-practice lawyers/accountants.
Recurring pain (daily admin: reminders, client-note digests, inbox triage),
existing willingness to pay (already pay for practice-management SaaS),
narrow/nameable audience (local directories, coach/therapist communities),
clear ROI (3-5hrs/week saved). Key differentiator for this buyer
specifically: client confidentiality means they *can't* use cloud AI for
notes — the offline-gemma4 privacy tier is a requirement for them, not a
compromise, unlike the general market where "runs locally" is just parity
with OpenClaw. Sell as "AI office assistant" (WhatsApp/Telegram reminders,
ringback call nudges, daily digest) — no code/API/"workflow" jargon in
positioning or UI copy for this segment.

**Pricing (unchanged from earlier analysis):** free tier (chat only) → Pro
$19 one-time or $6/mo (voice, messaging, automations) → Privacy/Offline tier
$29 one-time or $9/mo (100% local gemma4, verified zero-cloud-spend
workflows). Realistic revenue target: $5-15K ARR in first 12 months, not
$50K — no existing distribution channel or named customer list yet; $50K is
a plausible year-2 outcome only if the local-reliability niche bet lands.

**Constraint: local concurrency ceiling (2026-08-04).** Not an artificial
Ollama concurrency cap — a memory ceiling. gemma4:12b resident weights + KV
cache eat most of 24GB unified mem on M4; a second concurrent 12b instance
either OOMs or Metal serializes it (no true parallel exec across two loaded
instances of the same model class). `OLLAMA_NUM_PARALLEL` batches requests
onto one loaded model but throughput/latency degrades hard past 1-2
concurrent requests on a 12b model on this hardware — it's time-sliced
batching, not real parallelism. Practical effect: the "100% local, zero
cloud spend" privacy tier can run one workflow's LLM step at a time
reliably; two scheduled workflows firing simultaneously will queue/stack
latency, not run in parallel. Scope the privacy tier's marketing and the
scheduler's concurrency limit (`jobs/worker.py`) around "one local LLM call
in flight at a time," not "N workflows fully parallel."

Confirmed via code read: this asymmetry is already intentional, not a bug.
`clients/cloud_client.py` (~line 780) fans out tool/subagent calls with
`ThreadPoolExecutor(max_workers=min(len(tool_calls), 4))` — real parallelism,
safe because OpenRouter handles concurrency server-side, no local memory
contention. `clients/ollama_client.py`'s tool-call loop is a plain sequential
`for call in tool_calls` — no thread pool. `tools/orchestrator_tools.py`'s
`ask_*` subagent dispatch uses `ThreadPoolExecutor(max_workers=1)` per call,
which is a timeout wrapper, not a fan-out point — subagent calls already
execute one-at-a-time regardless of local/cloud. Do not "fix" the local loop
to match cloud's 4-way parallel pattern — that would reintroduce the OOM/
serialization risk described above.

---

## 0b. Document Agent Architecture (Decided 2026-08-04)

Locks in scope for the primary Tauri surface: **one domain, private document
work — not a general-purpose agent.** §7b/7c/7d cover *what* the doc engine
does (crate stack, per-format ops); this section covers *how it's
orchestrated* so gemma4:12b's known ceiling (unreliable past 2-3 chained
tool calls, single serialized inference slot per §0 concurrency note) never
turns into an open-ended 20-60-tool agentic loop.

**Fixed pipeline, not open tool-choice:**

```
ingest → extract (OCR/parse) → chunk/embed → retrieve → ground → synthesize
```

gemma4 is called at exactly two fixed slots — intent classify (which pipeline
variant) and final synthesis (answer from already-grounded snippets). It
never chooses which tool to call next; code chooses. Most queries (find
clause, extract date, list parties) resolve entirely in Tier A below, with no
gemma4 call at all.

**Tiering (extends §9b's non-LLM tools table with the "why"):** gemma4 is a
single serialized inference queue — the only genuine parallelism available
is fanning out everything that isn't gemma4.

| Tier | Examples | Parallel-safe | gemma4 involved? |
|------|----------|-----------------|-------------------|
| A — deterministic code | PDF/doc extraction, regex field extraction, citation/span grounding, format conversion, schema validation | Yes — pure code, no model weights | No |
| B — small dedicated model daemon | surya OCR daemon, `nomic-embed-text` daemon | Yes — separate weights/process from gemma4, can run concurrently with it | No |
| Serialized | Intent classify, final synthesis | No — single queue | Yes, only here |

Orchestrator fans out Tier A + Tier B concurrently, collects structured
results, then makes at most one gemma4 call at the end (synthesis over
pre-extracted data) — never a per-subagent loop.

**Anti-hallucination gates (enforced in code, not by prompting alone):**
- No answer without a grounded source span; if retrieval returns no snippets, skip the gemma4 call entirely and return "not found in document" directly.
- Synthesis prompt scoped to "answer only from provided text, else say not found" — belt, not the whole guardrail.
- Post-hoc fuzzy-match of gemma4's claim back against the source span (Tier A, no LLM); mismatch → discard the answer, surface "uncertain," don't show it.
- System prompt bans world-knowledge answers — retrieval-empty-skip (above) is the enforcement, the prompt line is backup only.
- Every answer shows its source quote/page in the UI — provenance the user (lawyer/doctor/therapist) can verify at a glance, not a trust claim.

**Conversational state lives in code.** Active document, prior extracted
facts, and follow-up reference resolution ("what about clause 3?") are
resolved by code before any gemma4 call — the model is not asked to
re-derive multi-turn state itself.

---

## 1. Security Hardening

| ID | Severity | What | Files | Effort | Verify |
|----|----------|------|-------|--------|--------|
| S1 | **RESOLVED (stale, verified 2026-08-04)** | Audit claimed WhatsApp bot binds `0.0.0.0:9236` no auth. Checked live: `whatsapp_bot.py:180` is `uvicorn.run(app, host="127.0.0.1", ...)` — already loopback-only. No fix needed | `tools-harness/whatsapp_bot.py:180` | n/a | confirmed via grep+read |
| S2 | **RESOLVED (stale, verified 2026-08-04)** | Audit claimed Kokoro daemon's `0.0.0.0:9237` bind needed switching to loopback. Checked live: the `0.0.0.0` bind is deliberate (comment at `kokoro_daemon.py:29-31` — ringback's docker container reaches it via `host.docker.internal`, not loopback; changing the bind would break ringback) and a `KOKORO_AUTH_TOKEN` gate (`_get_auth_token`/`_auth_ok`, `:36-46`) already closes it. No fix needed | `tools-harness/kokoro_daemon.py:29-46` | n/a | confirmed via read |
| S3 | **RESOLVED (stale, verified 2026-08-04)** | Audit claimed hardcoded `jimkalinov@gmail.com`/`Jimkali90#` in client React. Repo-wide grep (`.tsx`/`.ts`/`.py`, excluding `.next`/`node_modules`) — zero hits. Already removed | `lib/auth-context.tsx`, `app/auth/signin/page.tsx` | n/a | grep, zero hits |
| S4 | **RESOLVED (stale, verified 2026-08-04)** | Audit claimed `/voice/ingest` has no auth check. Checked live: `chat_ui.py:2398` calls `_require_auth(request)` as its first line. No fix needed | `tools-harness/chat_ui.py:2398` | n/a | confirmed via read |
| S5 | **DONE (2026-08-04)** | Verified live: `.env:55,58` is gitignored (`.gitignore:3`), never a real leak. But `imessage_search.py:471` *did* hardcode the real number as a source-level default — real leak into tracked git history. Fixed: default now reads `IMESSAGE_DEFAULT_TARGET` from `.env` (already existed), no literal in source. Also scrubbed the number from a stale example comment at line ~520. 19/19 imessage-tagged tests pass | `tools-harness/tools/imessage_search.py:471` | done | `IMMESSAGE_DEFAULT_SENDER` resolves from env, confirmed live import |
| S6 | **RESOLVED (stale, verified 2026-08-04)** | `tests/test_telegram_routing_sweep_round2.py` no longer exists (renamed/removed). Live grep of all tracked `*.py` for the chat ID: zero hits — only appears in gitignored `.env`. Not a current leak | — | n/a | `git grep` for chat ID across tracked source, zero hits |
| S7 | **DONE (2026-08-04)** | Confirmed live via dry-run: `check_python_code_safety()` bypassed cleanly by `__import__("subprocess")`, `getattr(os,"system")`, `os.__dict__["system"]`, `().__class__.__bases__[0].__subclasses__()` — zero exceptions raised, real and current. Fixed: block `__import__`/`getattr`/`setattr`/`delattr`/`vars`/`globals`/`locals` calls, plus any dunder attribute/name access (blocks `__dict__`, `__class__`, `__builtins__`, etc.). Re-ran same 4 payloads post-fix — all blocked; legit code (`pandas`, `json`, plain calc) still passes; 21/21 in `test_security_boundaries.py` + `test_bch_imessage_gates.py` pass | `tools-harness/tools/tool_policy.py:38-73` | done | 4 known bypass payloads all raise `ValueError`, legit code unaffected, full test suite green |
| S8 | **DONE (2026-08-04)** | `KnowledgeBase.search()` filtered `source` in Python *after* fetching top_k*4 candidates instead of via LanceDB `.where(prefilter=True)`. Fixed: `source_filter` now restricts the candidate set natively before vector scoring. Verified with a live dry-run (mocked embeddings, mixed-source table, asserted filtered search returns only the target source) — 40/40 existing KB-dependent tests still pass. Scope note: did **not** add a `client_id` field/multi-client isolation — no caller needs it today, single-user-local design, would be YAGNI ahead of an actual multi-client feature | `tools-harness/store/knowledge_base.py:200-226` | done | `search(query, source_filter=X)` returns only rows where `source == X`, confirmed live |

---

## 2. Configuration & .env.example

| ID | Severity | What | Files | Effort | Verify |
|----|----------|------|-------|--------|--------|
| C1 | **BLOCKER** | No `.env.example` — new user has zero template for 7+ required API keys | `tools-harness/.env.example` (new) | 30 min | File exists, all keys documented with "where to get" links |
| C2 | **BLOCKER** | `KOKORO_ONNX_PATH`/`KOKORO_VOICES_PATH` crash on KeyError when absent | `tools-harness/harness_tts.py:23`, `tools-harness/telegram_bot.py:77` | 2 lines each | Import succeeds without env vars; TTS triggers clean error |
| C3 | **HIGH** | `DEEPSEEK_API_KEY` not checked by `doctor.py` — cloud pipeline silent-fails | `tools-harness/doctor.py` | 10 min | `python doctor.py` shows DEEPSEEK_API_KEY status |
| C4 | **HIGH** | `doctor.py` also skips `EXA_API_KEY` and `TAVILY_API_KEY` (hard-crash vars) | `tools-harness/doctor.py` | 10 min | Same — all crash-causing env vars in doctor output |
| C5 | **MEDIUM** | `google-mcp/server.js` fallback defaults hardcoded to `/Users/kalinovdameus/...` | `google-mcp/server.js:8-9` | 2 lines | Use `path.resolve(__dirname, 'credentials.json')` |
| C6 | **LOW** | `secure=False` on session cookie | `tools-harness/chat_ui.py:441` | 1 line | Cookie shows `Secure` flag in devtools when accessed over HTTPS |

---

## 3. PII & Personal Data Extraction

All hardcoded emails, phone numbers, chat IDs, and personal identifiers — replaced with
env var defaults or removed.

| ID | Severity | What | Files | Fix |
|----|----------|------|-------|-----|
| P1 | **HIGH** | `jimkalinov@gmail.com` in User-Agent and contact email | `tools-harness/tools/discovery_sources.py:5,7` | Replace with `CLIXEN_CONTACT_EMAIL` env var, default `clixen@localhost` |
| P2 | **HIGH** | `jimkalinov@gmail.com` hardcoded as TARGET_EMAIL | `tools-harness/jobs/tech_brief_job.py:26` | `os.environ.get("TECH_BRIEF_EMAIL", "")` |
| P3 | **HIGH** | `jimkalinov@gmail.com` in automation catalog defaults | `tools-harness/automation_catalog.py:181,189` | `os.environ.get("CLIXEN_EMAIL", "owner@local.dev")` |
| P4 | **HIGH** | `jimkalinov@gmail.com` in workflow store default | `tools-harness/store/workflow_store.py:658` | Same as P3 |
| P5 | **HIGH** | `kalinovjim@gmail.com` hardcoded as watched sender | `tools-harness/jobs/inbox_monitor_job.py:51` | Read from `WATCHED_SENDERS` env var |
| P6 | **HIGH** | `["jayveedz19@gmail.com", "kalinovjim@gmail.com"]` hardcoded | `tools-harness/jobs/morning_briefing_job.py:48` | Same as P5 |
| P7 | **HIGH** | Same hardcoded senders in tool registry defaults | `tools-harness/tools/_registry/executors.py:416`, `tools-harness/tools/_registry/imports.py:394,404,418` | Same as P5 |
| P8 | **HIGH** | Username regex `jayveedz|kalinovjim` in router patterns | `tools-harness/clients/router_patterns.py:484` | Build from `WATCHED_SENDERS` env var, parse email local-parts |
| P9 | **LOW** | Real email in `email_watch.py` sample texts | `tools-harness/scripts/email_watch.py:152-153` | Anonymize to `watched@example.com` |

---

## 4. Cross-Platform Hardening

| ID | Severity | What | Files | Effort | Verify |
|----|----------|------|-------|--------|--------|
| X1 | **BLOCKER** | `gemma4:12b-mlx` hardcoded as default local model in 65+ call sites (verified via grep 2026-08-04) — Intel Macs and Linux get GGUF (`gemma4:latest`), not MLX. `ollama_client.py` already exposes a single-source-of-truth `DEFAULT_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "gemma4:12b-mlx")` constant with a comment telling call sites to import it — the 65+ sites just aren't using it yet | `ollama_client.py:75`, all 10 specialists, `websearch.py:471`, `deep_research.py:26`, `telegram_bot.py:1036`, `_summarize.py:608` | Env var already exists; remaining work is migrating 65+ sites to import the constant | Set `OLLAMA_DEFAULT_MODEL=gemma4:latest` → all local calls use it |
| X2 | **BLOCKER** | All 7 launchd plists hardcoded to `/Users/kalinovdameus/Developer/clixen/` — non-transferable | `tools-harness/launchd/*.plist` | 1 hr | Template plist + `install-launchd.sh` generation script |
| X3 | **HIGH** | `/opt/homebrew/bin/node` hardcoded (Apple Silicon Homebrew path) | `messaging_supervisor.py:32`, `messaging_supervisor.sh:27` | 2 lines each | `which node` or `NODE_BIN` env var |
| X4 | **HIGH** | `/opt/homebrew/bin/python3.12` in task worker plist — different Python paths on other machines | `com.clixen.task_worker.plist:12` | 1 line | Use `.venv/bin/python` like other plists |
| X5 | **HIGH** | `caffeinate` in task_worker plist (macOS-only) | `com.clixen.task_worker.plist:10` | Remove or gate | Plist loads on Linux without caffeinate line |
| X6 | **HIGH** | `screencapture` hardcoded (macOS-only, `/usr/sbin`) | `telegram_bot.py:965`, `peekaboo.py:96` | 5 min | `sys.platform == 'darwin'` guard, fallback message |
| X7 | **HIGH** | `afplay` TTS playback — macOS only, no fallback for Linux | `harness_tts.py:29`, `brabble_hook.py` | 30 min | Platform-detect, fallback to `ffplay` / `playsound` |
| X8 | **HIGH** | `say` fallback TTS produces silence on Linux | `brabble_hook.py:254-261` | 10 min | Add `log.warning()` when both Kokoro and `say` fail, document alternatives |
| X9 | **MEDIUM** | `open -a Docker` in docker_watchdog (macOS-only) | `docker_watchdog.sh:5` | 2 lines | Gate: `[[ $(uname) == Darwin ]] && open -a Docker || true` |

---

## 5. Documentation & Onboarding

| ID | Severity | What | Files | Effort | Verify |
|----|----------|------|-------|--------|--------|
| D1 | **BLOCKER** | No onboarding guide mentions API key setup — user must reverse-engineer from code | `README.md` | 30 min | New section "Prerequisites: API Keys" listing all 7+ keys |
| D2 | **BLOCKER** | No model-pulling instructions in Quick Start | `README.md` | 10 min | `ollama pull` commands in Quick Start |
| D3 | **HIGH** | `doctor.py` checks wrong LanceDB dir (`store/lancedb/` → actual is `data/*.lance`) | `tools-harness/doctor.py:74-78` | 2 lines | Doctor reports correct LanceDB paths |
| D4 | **MEDIUM** | Stale `src.g4l` import comment in `chat_ui.py` | `tools-harness/chat_ui.py:10-12` | 1 line | Remove comment, `src/g4l/` no longer exists |
| D5 | **LOW** | OAuth token paths in `gmail.py` use `~/Developer/gmail-mcp/` — old name | `tools-harness/tools/gmail.py:42,45` | Document | Mention in README or add to `.env.example` |

---

## 6. Code Quality & Edge Cases

| ID | Severity | What | Files | Effort | Verify |
|----|----------|------|-------|--------|--------|
| Q1 | **MEDIUM** | No port-conflict handling — daemons crash-loop on bind failure | `core.py:77-108`, all bind sites | 1 hr | Kill chat_ui, start second → clean error not crash-loop |
| Q2 | **MEDIUM** | Hardcoded user paths in docstrings (portability confusion) | `mcp_server.py:19-20`, `brabble_hook.py:14`, `orchestrator_tools.py:52` | 5 min | Anonymize to `<repo>/...` or `$REPO_ROOT/...` |
| Q3 | **LOW** | Loopback auto-auth bypass — any local process is owner | `chat_ui.py:483-491` | Document | Acceptable for single-user; document in multi-user section |
| Q4 | **LOW** | `_UGENT_ENRICHED` references entirely different project (`~/Developer/ugent-app/...`) | `telegram_bot.py:234` | 1 line | Make env-configurable or remove |
| Q5 | **LOW** | `generate_pdf.py` — throwaway script with hardcoded paths | `generate_pdf.py:9,160` | 5 min | Move to `scripts/ad-hoc/` or use `Path.home()` |
| Q6 | **LOW** | `dry_run_test.py` hardcoded path in test | `dry_run_test.py:18` | 1 line | Use `Path.cwd()` or `Path(__file__)` |

---

## 7. Housekeeping

| ID | Severity | What | Files | Effort | Verify |
|----|----------|------|-------|--------|--------|
| H1 | **LOW** | `tests/verify_qwen_tool_selection.py:39` — hardcoded user path in test fixture | 1 line | `Path.home()` |
| H2 | **LOW** | `docs/agents/orchestrator.md:35-37` — broken `file:///Users/kalinovdameus/...` links | 2 lines | Replace with relative paths |
| H3 | **LOW** | GitHub dependabot shows 77-112 vulnerabilities (Next.js + npm deps) | `package.json`, `package-lock.json` | Ongoing | Separate issue — monitor dependabot, bump deps |
| H4 | **LOW** | `uv.lock` package name needs regeneration after `pyproject.toml` cleanups | `uv.lock` | 30s | `uv lock` (already name-fixed, regeneration optional) |
| H5 | **INFO** | Node.js `tsx` v7.0 requirement is bleeding-edge | `tsconfig.json`, `package.json` | Document | Note in README that `npm install` pins versions |
| H6 | **INFO** | `ONBOARDING_FLOW_MOCKUP.md` describes NextAuth cloud flow — not implemented | `ONBOARDING_FLOW_MOCKUP.md` | Document | Archive as aspirational design doc |

---

## Execution Order

```
Phase 1 — Security (blocking, 2 hours)
  S1, S2, S3, S4, S5, S6, S7

Phase 2 — Configuration (blocking, 2 hours)
  C1, C2, C3, C4, C5, C6
  (Creates .env.example, fixes crash paths, updates doctor.py)

Phase 3 — PII Extraction (1 hour)
  P1 – P9

Phase 4 — Cross-Platform (4 hours)
  X1 – X9
  (Model config env var, plist templates, binary path fixes, platform guards)

Phase 5 — Documentation (1 hour)
  D1 – D5

Phase 6 — Code Quality & Housekeeping (2 hours)
  Q1 – Q6, H1 – H6

Total estimated: ~12 hours
```

---

## 7b. Tauri Privacy-Tier Build Requirement (2026-08-04, corrected 2026-08-04)

**Correction:** the original entry here misread `agents/local_agent_nodes.py:863-877`.
Re-verified via `git log -p` (single commit touches this file — no regression,
this was a misread from the start, not a later behavior change): the
escalation gate is `error_count >= 3 and is_cloud_model(state.current_model)`
— it only fires **cloud → cloud-fallback** (a flaky/misbehaving DeepSeek call
gets one retry on GPT), never local(gemma4) → cloud. There is no code path
in this function that promotes an offline gemma4 session to a cloud model.
The "despite the name 'local agent,' it unconditionally escalates to cloud"
claim below was wrong; keeping the requirement anyway as defense-in-depth,
not because this specific mechanism is a live confidentiality bug.

**Requirement (retained as defense-in-depth):** for the privacy-tier Tauri
build, don't rely solely on this one gate staying correct forever — ship
without `clients.cloud_client` importable/reachable at all in that build
target, so a confidential session fails/stops locally on repeated errors
instead of any future code change accidentally reintroducing a local→cloud
path. Re-audit any other local-agent-labeled code path for a similar pattern
at Tauri build time; this file audits clean today, but "clean today" isn't
a substitute for the build-target guarantee.

---

## 7bb. `clixen_search` — status check (verified 2026-08-04)

External chat advice proposed a `clixen_search` tool (LanceDB hybrid vector+FTS,
`client_id`-filtered, same `EXECUTORS` pattern as `redact_document()`) plus a
vault (`ClixenVault/`, Argon2id passphrase, `assert_path_in_vault()`,
`license.rs`/Lemon Squeezy paywall). Checked against actual repo state before
acting on any of it — most was aspirational, not built:

- `redact_document()` — **real**, `tools-harness/tools/pii_redact.py:66`,
  registered in `EXECUTORS`. The one piece of that advice already shipped.
- `clixen_search`, `ClixenVault`, `assert_path_in_vault()`, `license.rs`,
  Argon2id — **zero hits repo-wide**. None of this exists. Not a gap in a
  half-built feature, just not started.
- `tauri-spikes/` — exists but is a bare `cargo new` skeleton (`main.rs` only,
  no Tauri framework wiring). No onboarding/paywall/license work is
  buildable yet; there's no app surface to attach it to.
- LanceDB hybrid+prefilter architecture claim in the advice — **verified live**
  against LanceDB 0.33 in this venv: native `.where(prefilter=True)`,
  `create_fts_index()`, and single-call `search(query_type="hybrid")` all work
  correctly (prefilter test: 1/1 correct row, no leak; hybrid test: correct
  row on both vector+text+filter combined). The architecture is buildable
  today — see S8 above for the concrete first step (fix `knowledge_base.py`'s
  post-hoc Python filter, which is the real gap, not a missing capability).
- Pricing in the external advice ($199 one-time / 7-day trial / Lemon Squeezy)
  conflicts with this roadmap's own §0 pricing ($19–29 one-time or $6–9/mo,
  dated 2026-08-03/04). Roadmap's number stands — no reason given in the
  external advice to override a dated, already-reasoned decision.

**Build order implied:** S8 (fix `knowledge_base.py` filter/schema) is the
correct next step — it's both the live security fix and the foundation
`clixen_search` needs. Vault/license/payment work stays blocked behind an
actual Tauri app surface existing, which it doesn't yet.

---

## 7c. Doc-processing crate stack (DECIDED 2026-08-04)

Embedded-in-Rust, no LGPL, no pandoc-style sidecar. Four ops per format
(create / edit / get style / get comments) for the privacy-tier build.

| Format | Crate(s) | License | Role split |
|--------|----------|---------|------------|
| DOCX | `docx-rs` | MIT | create / edit / get style / get comments |
| PDF | `lopdf` + `printpdf` | both MIT | `lopdf` = read/edit/style/comments; `printpdf` = create [annotations] |
| EXCEL | `calamine` + `rust_xlsxwriter` + `umya-spreadsheet` | calamine MIT, xlsxwriter MIT OR Apache-2.0, umya MIT | calamine = read (indexing), xlsxwriter = create/edit (no in-place edit — read then rewrite), umya = full style/comments |
| PAGES | convert via macOS `textutil` → docx, then `docx-rs` | n/a | `.pages` = Apple-private IWA protobuf zip; no MIT parser exists. Option B (zip + quick-protobuf reverse-engineer) rejected for V1 |

Still open → now drafted: Tauri command shapes for the two hardest ops.

**PDF `get_comments` (lopdf).** PDF comments are annotation objects, not a
sidecar. Walk each page's `/Annots` array, filter `Subtype /Text` and
`/Highlight` (popup/ink/link/square are different annotation kinds, not
comments). Extract per annotation: `/Contents` (comment body, may be hex or
stream — `Content::Stream` decode), `/T` (author, often unset in exported
PDFs — default to "anonymous"), `/Subj` (subject), `/Rect` (position, for
anchoring back to the page), and the page number. Return `[{page, author,
rect, text}]`. Skip form-field `/Widget` annotations (those belong to
`get_style`/form-fill, not comments). Watch: comment text is sometimes
UTF-16BE in the literal stream — detect the BOM before UTF-8 decode.

**Excel `get_styles` (umya-spreadsheet).** `worksheet.get_style(ref)` returns
the cell style: font (bold/italic/underline, size, color RGB), fill (solid
color, pattern type), `numFmt` (the number format string — needed to
distinguish currency/percent/date cells), and alignment (wrap, halign,
valign). Expose `get_styles(path, sheet, range?)` returning a JSON matrix
keyed by cell ref so the model can match "red bold cell = flagged input"
conventions (open-cowork financial-modeling color code: blue=inputs,
black=formulas, green=same-workbook links, red=external). Note: umya reads
the raw XML; merged-cell styling lives on the top-left cell only. `calamine`
is NOT used here — its read path exposes value types, not formats.

---

## 7d. Doc-skill learnings from open-cowork + kimi-skills (2026-08-04)

Studied `OpenCoworkAI/open-cowork` `.claude/skills/{docx,pdf,pptx,xlsx}` (MIT)
and `thvroyal/kimi-skills` `skills/kimi-{docx,pdf,xlsx}` (unlicensed — patterns
only, no code lifted). Cross-format patterns worth keeping for the Tauri build:

1. **Original-diff validation** — validate against real XSDs but only fail on
   errors the *original* file didn't already have (kills false positives).
2. **JSON contract + validate-before-mutate + post-write regression check** —
   model emits structured intent, script validates every key before touching
   the file, then re-measures output and fails on regression. No second model call.
3. **Two-direction render loop** — model-authored geometry made visible
   (validation overlay images) + machine-checked before use (PDF FORMS.md).
4. **De-noise the DOM before the model edits** — merge adjacent runs, strip
   proofErr, escape smart quotes, pretty-print. Radical rule: edit raw XML
   directly, don't script content changes.
5. **LibreOffice as the unpaid formula/render engine** — one headless LO macro
   gives a real calculator for xlsx (openpyxl writes formulas as strings) and
   renders docx/pptx→images for visual QA.
6. **Element-order repair** (kimi-docx `element_order.py`) — stable-sort
   children against schema order, unknown elements kept at end (lossless);
   force `w:sectPr` last in body. Single best fix for "Word says unreadable content".
7. **xlsx error taxonomy** — scan for the 7 error strings, ban dynamic-array
   fns (FILTER/UNIQUE/XLOOKUP/LET/LAMBDA break Excel ≤2019), flag SUM/AVERAGE
   over ≤2-cell ranges (pandas off-by-one signature), never openpyxl a pivot file.
8. **Native editable charts** — embed data as `c:strCache`/`c:numCache` so charts
   render without external Excel refs; heatmap/3D fall back to matplotlib PNG.
9. **Comments/track-changes** — modern Word comments span 5 XML files
   (comments + commentsExtended + commentsIds + commentsExtensible + people)
   chained by paraId/durableId; text-anchored, char-precise run splitting.
10. **Regenerate, don't patch** — failed validation = rebuild from scratch.

Implemented in Python harness (2026-08-04, `tools/doc_quality.py` + skills
"Check Document Quality" / "Repair DOCX" in `skills_data/docs.py`): #6 docx
element-order repair, #7 xlsx formula/forbidden-fn/small-aggregate scan,
structural docx validation (parts/rels/sectPr), PDF blank/low-content page
scan. Deferred to Rust build (§7c): #1 XSD validation (no Python equivalent),
#8 native charts, #9 comments, #5 LibreOffice recalc, #3 render loop.

---

## 8. Prebuilt Workflow Templates (Desktop App Packaging)

Scope note: this section and §9 are messaging/scheduling automations
(reminders, digests, watchers) — a separate feature surface from the
document agent in §0b. Don't reuse this section's open-ended
trigger/condition/branch primitives as a template for the doc agent; §0b's
fixed pipeline is the intended shape there, not B1-B10 generalized routing.

Generic automations to ship as one-click templates in the packaged app — no
Clixen-specific setup (SIP number, DoorDash account) required, just fill-in
config on top of existing `jobs/worker.py` action_types
(`telegram/notification/webhook/email/tool_call`).

| ID | Workflow | What | Built on |
|----|----------|------|----------|
| W1 | Daily digest | Summarize inbox + calendar + news into one message each morning | `email_watch.py`, `websearch.py`, telegram/whatsapp send |
| W2 | Inbox triage | Auto-draft replies to routine emails, flag ambiguous ones | `tools/gmail.py`, `harness.py` intent dispatch |
| W3 | Meeting prep | Before each calendar event, pull related emails/docs, send brief | Google Calendar + Gmail tools |
| W4 | File-drop pipeline | Watch folder → auto-summarize/convert dropped docs → send back | existing doc converters (pdf/docx/xlsx) |
| W5 | Price/availability watcher | Track a URL, alert on change | `websearch.py` pipeline |
| W6 | Reminder with escalation | Notification first, phone call if unacknowledged after N min | `jobs/worker.py` scheduling + `connector_ringback.py` |
| W7 | Weekly report generator | Pull data from a sheet/doc, auto-format summary, send on schedule | `workflow_store.py` scheduled automations |
| W8 | Research digest | Topic in, weekly web-search + summarize results | `tools/websearch.py` |
| W9 | Form-fill assistant | Recurring form (expense/timesheet) auto-filled from template + data | `agents/local_agent_tools.py` `detect_form_fields→fill_form` |
| W10 | No-code webhook relay | External event → routed to Telegram/email/call by simple condition | `action_type="webhook"` generic handler |

Ship W1, W5, W6, W9 as default-enabled templates at launch (strongest
recurring-pain/ROI fit per market research); rest as an opt-in gallery.

---

## 9. Base Workflow Primitives

Generic, parameterized building blocks — W1-W10 above are presets built on
top of these, not separate code paths. `jobs/worker.py`'s existing
action_types (`telegram/notification/webhook/email/tool_call`) already cover
delivery; what's missing is the trigger/condition/branch half.

| ID | Primitive | What | Generalizes |
|----|-----------|------|--------------|
| B1 | Schedule → Action | Cron trigger + any single tool_call | Any "do X every Y" |
| B2 | Watch → Diff → Notify | Poll any source, compare to last state, alert only on change | W5 (URL → any source: file/API/DB row/RSS) |
| B3 | Condition → Branch → Notify | If/else routing to different channels based on a rule | Escalation, triage, approval-routing |
| B4 | Multi-step Pipeline | Fetch → transform → summarize → deliver, each step swappable | W1/W7/W8 (digest/report/research) |
| B5 | Threshold Alert | Numeric/state trigger crosses a value → notify | Price watch, health check, budget alert |
| B6 | Approval Gate | Automation pauses, waits for user reply/confirm before proceeding | Any destructive/high-stakes action |
| B7 | Ingest → Extract → Store | Incoming file/message → pull structured data → append to sheet/DB | Expense tracking, lead capture, log aggregation |
| B8 | Escalation Ladder | Reminder w/ user-defined channel sequence (notify→email→SMS→call) | W6, generalized beyond fixed phone-call step |
| B9 | Template-fill from Data Source | Form/doc auto-filled from any structured input | W9, generalized beyond expense/timesheet |
| B10 | Webhook In → Route by Payload | External event, condition on payload shape, dispatch anywhere | W10, generalized beyond one fixed condition |

Build B1-B6 first (trigger/condition primitives) — B7-B10 mostly reuse
existing `agents/local_agent_tools.py` and `store/workflow_store.py` plumbing
already built for W1-W10.

### 9b. Local-First Non-LLM Tools (2026-08-04)

gemma4:12b is the actual bottleneck (single inference slot, 24GB ceiling —
see Section 0 concurrency note). Any task that doesn't need generation/
reasoning should run as a deterministic tool instead, for two reasons: it's
parallel-safe (separate process/small-model, not competing for gemma4's
memory slot), and it's more reliable (no hallucination risk, vs. gemma4's
known degradation past 2-3 chained tool calls). Design rule: reserve gemma4
for synthesis/dialogue/reasoning only; push classification, extraction,
conversion, and search off onto narrow tools.

| Tool | Use | Parallel-safe? | Status |
|------|-----|-----------------|--------|
| ffmpeg | Audio/video transcode, extraction, format conversion | Yes — separate subprocess, no shared model memory | Already used ad hoc, not yet a registered agent tool |
| OCR (tesseract or similar) | Scanned doc → text | Yes — CPU-bound, no shared model memory | `gemma4:12b-mlx` currently handles OCR intent (see CLAUDE.md Models table) — candidate to replace/supplement with a dedicated OCR engine to free gemma4 for that slot |
| `nomic-embed-text` | Embeddings — semantic search, dedup, similarity | Yes — small single-forward-pass model, already separate from gemma4 | Already in use (`tools/semantic_files.py`, `store/knowledge_base.py`) |
| Small classifier (intent/triage) | Route/tag without generation (e.g. distilled BERT-class model) | Yes — tiny model, low memory footprint | Not yet built — candidate to offload `router.py` classification work currently done by gemma4 |

Concrete next step: audit `router.py`'s `classify()`/`classify_telegram()` —
currently uses gemma4 as intent classifier per CLAUDE.md Models table; a
small dedicated classifier model would free gemma4 entirely from the
routing path and could run in parallel with whatever gemma4 is already
doing, since it's a separate small model.

---

## Verification Checklist

Before marking this roadmap complete, verify:

- [ ] Fresh clone on a different macOS user account → `python chat_ui.py` starts and serves UI
- [ ] No `/Users/kalinovdameus/` in any source file (except expected config files)
- [ ] `python doctor.py` passes with only expected warnings (no missing models, no unset critical keys)
- [ ] All network services bind to `127.0.0.1` (verify: `lsof -iTCP -sTCP:LISTEN | grep -v 127.0.0.1` empty)
- [ ] `grep -r "jimkalinov\|jayveedz19\|kalinovjim\|raymonvillemaxi\|benouchecapierre\|+18574261739\|8538224711" --include="*.py" --include="*.ts" --include="*.tsx" --include="*.sh" --include="*.plist" tools-harness/` returns zero hits (except `.env`)
- [ ] `export OLLAMA_DEFAULT_MODEL=gemma4:latest` → all local model calls use GGUF format
- [ ] `.env.example` documents every required and optional env var with "where to get it" links
- [ ] No hardcoded personal credentials in any tracked source file (React, Python, config)
