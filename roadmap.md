# Clixen — Multi-User & Cross-Platform Roadmap

Generated from audit of hardcoded paths, platform assumptions, security leaks, env var
gaps, and startup failures. Goal: runnable on any machine, by any user, with minimal
friction.

---

## 1. Security Hardening

| ID | Severity | What | Files | Effort | Verify |
|----|----------|------|-------|--------|--------|
| S1 | **CRITICAL** | WhatsApp bot binds `0.0.0.0:9236` with no auth — exposed to LAN | `tools-harness/whatsapp_bot.py:166` | 1 line | `curl localhost:9236/health` returns 200 from loopback only |
| S2 | **CRITICAL** | Kokoro TTS daemon binds `0.0.0.0:9237` with no auth — LAN-exposed TTS endpoint | `tools-harness/kokoro_daemon.py:185` | 1 line | Same as S1, on 9237 |
| S3 | **CRITICAL** | Hardcoded email+password in client-side React (`jimkalinov@gmail.com` / `Jimkali90#`) — ships to every browser | `lib/auth-context.tsx:52`, `app/auth/signin/page.tsx:84,286` | 10 min | Grep for password string — zero hits after fix |
| S4 | **HIGH** | `/voice/ingest` endpoint has no auth check — broadcast injection | `tools-harness/chat_ui.py:2195` | 2 lines | POST /voice/ingest returns 401 from non-localhost |
| S5 | **HIGH** | Real phone `+18574261739` in source + test files | `tools-harness/tools/imessage_search.py:471`, `tools-harness/.env:55,58` | 5 min | Grep for phone number — only `.env` |
| S6 | **HIGH** | Real Telegram chat ID `8538224711` in test file | `tools-harness/tests/test_telegram_routing_sweep_round2.py:45` | 1 line | Grep for chat ID — zero hits in source |
| S7 | **MEDIUM** | Python REPL AST check bypassable via `getattr(__builtins__, 'ev'+'al')` | `tools-harness/tools/tool_policy.py:38-67`, `tools/tool_policy.py` `check_python_code_safety()` | 30 min | Unit test with bypass payload — caught |

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
| X1 | **BLOCKER** | `gemma4:12b-mlx` hardcoded as default local model in 90+ call sites — Intel Macs and Linux get GGUF (`gemma4:latest`), not MLX | `ollama_client.py:70`, all 10 specialists, `websearch.py:471`, `deep_research.py:26`, `telegram_bot.py:1036`, `_summarize.py:608` | 1 env var + 90+ sites | Set `LOCAL_MODEL=gemma4:latest` → all local calls use it |
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

## Verification Checklist

Before marking this roadmap complete, verify:

- [ ] Fresh clone on a different macOS user account → `python chat_ui.py` starts and serves UI
- [ ] No `/Users/kalinovdameus/` in any source file (except expected config files)
- [ ] `python doctor.py` passes with only expected warnings (no missing models, no unset critical keys)
- [ ] All network services bind to `127.0.0.1` (verify: `lsof -iTCP -sTCP:LISTEN | grep -v 127.0.0.1` empty)
- [ ] `grep -r "jimkalinov\|jayveedz19\|kalinovjim\|raymonvillemaxi\|benouchecapierre\|+18574261739\|8538224711" --include="*.py" --include="*.ts" --include="*.tsx" --include="*.sh" --include="*.plist" tools-harness/` returns zero hits (except `.env`)
- [ ] `export LOCAL_MODEL=gemma4:latest` → all local model calls use GGUF format
- [ ] `.env.example` documents every required and optional env var with "where to get it" links
- [ ] No hardcoded personal credentials in any tracked source file (React, Python, config)
