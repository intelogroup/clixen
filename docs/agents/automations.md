# Automations & Task Worker (2026-07)

Background work runs through `jobs/worker.py`'s `main()`, inside `core.py`'s `task_worker` thread — one poll loop (default 10s interval) covering two independent systems:

**1. `job_queue`** (`jobs/job_queue.py`, SQLite) — immediate, one-shot tasks (e.g. `doc_summarizer`, `price_tracker`, `image_upscale` via `TASK_ROUTING` in `clients/router.py`). `claim_next()` atomically claims one job (`BEGIN IMMEDIATE` transaction), no batch/limit param — `worker.py` loops up to 5 claims per cycle. `mark_done`/`mark_failed` record outcome.

**2. `workflow_store`** (`store/workflow_store.py`, SQLite) — durable, recurring/scheduled automations. Already uses APScheduler's `CronTrigger` internally for correct cron-math (next-fire-time, timezone handling via IANA names resolved from `USER_TIMEZONE`/`TZ`/`/etc/localtime`) — it is NOT a from-scratch reimplementation, it's a mature data layer built on top of a real scheduling library.
- `claim_due_workflows(limit=10)`: atomically claims active instances whose `next_run_at` has passed, advances recurring ones to their next fire time (or pauses one-shot instances with no next run), and — if a workflow is >15 minutes overdue (e.g. process was down) — reschedules from *now* rather than replaying the missed interval, to avoid a cascade of backlogged fires. Returns full instance dicts.
- Each claimed instance is dispatched via `jobs/handler_registry.dispatch(instance)`, which looks up `instance["automation_id"]` and calls the registered `handle(instance) -> dict`. Result persisted via `update_workflow_instance(id, last_result=result)`.
- **14 builtin automations** seeded idempotently on every `chat_ui.py` startup (`seed_builtins()`, `store/workflow_store.py:_BUILTIN_WORKFLOWS`): `email.watch_sender`, `email.daily_summary`, `email.attachment_archive`, `inbox.monitor_attachments`, `briefing.morning`, `study.usmle_daily`, `tech_brief`, `golden.queries`, `health_check`, `reddit_intel`, `subagent_audit`, `daily_digest`, `science_scout`, `world_monitor` — each with its own dedicated handler file under `jobs/handlers/`, registered by exact id in `worker.py:_setup_handler_registry()`.
- **User/LLM-created automations** (via `tools/automation_tools.py:create_automation`, exposed through `ask_automation_agent` — reachable from Web UI, Telegram, WhatsApp, and voice/Brabble, since the orchestrator's tool list is channel-agnostic) all share **one** `automation_id`: `"user.automation"`. One generic handler (`jobs/handlers/user_automation.py`) reads `action_type`/`config` directly off the instance row — not off the automation_id — to decide what to do: `telegram` → `tools/telegram_send.send_telegram()`, `notification` → `workflow_store.add_notification()`, `http_webhook` → `requests.post(config["url"], json=config.get("payload"))`, `email` → `EXECUTORS["send_email"]`.

**Bug history (2026-07), fixed and verified live against the running process (not just unit tests)**:
- `worker.py` called `job_queue.poll_pending(limit=5)`, `job_queue.update_status(...)`, `workflow_store.poll_due_actions()`, `workflow_store.get_workflow()` — none of these exist; renamed/removed API drift left over from an earlier version of both stores. `poll_due_actions()`'s intended replacement (`claim_due_workflows()`) also has an incompatible return shape (list of full instance dicts vs. the old code's expected `(wf_id, action_name)` tuples), and the "actions" sub-list concept `_execute_workflow_action` was written against never existed in the current schema at all — `workflow_instances` rows carry `task_name`/`action_type`/`config` directly, no nested `actions` list.
- `create_automation` previously minted a synthetic per-call `automation_id` (e.g. `"user.schedule.telegram"`) that no handler could ever match — automations created this way would report success to the user but silently never fire their action (permanent `KeyError` on every due cycle, now at least visible via `last_result`). Fixed by the single `"user.automation"` id + generic handler.
- `claim_due_workflows()` computed `last_run_at` on the dict it returns to the caller but never included it in the `UPDATE` statement — the DB column stayed `NULL` forever regardless of fire count, so `list_automations` always reported "last_run=never". One-line fix to the `UPDATE`.
- Handler failures (unregistered handler or handler exception) are now recorded as `{"success": False, "error": ...}` via `last_result` rather than left stale.

**Not built**: no jsonschema/pydantic validation of tool-call arguments anywhere in `tools/registry.py:execute_tool()` — the `ALL_TOOLS` parameter schemas are descriptive metadata for the LLM API only. Enforcement that does exist is a denylist guardrail (dangerous filesystem paths / shell command substrings) plus a blanket exception→string wrapper, not schema typing.

**Agent reachability (2026-07-31)**:
- **Orchestrator direct** (`harness.py` `orchestrator_tools`): list/get/history/pause/resume automation, create/trigger/delete/update, approvals (list_pending/approve/reject), `query_subagent_findings`, `create_workflow`, `list_available_pipelines`, + observability: `get_automation_stats`, `get_workflow_failures`, `get_workflow_logs`, `get_integration_templates`.
- **Automation subagent** (`ask_automation_agent`, harness.py automation-intent `_tool(...)`): same as orchestrator + `delete_workflow_permanently`, `list_workflows`, plus email/pdf/telegram tools for gathering content. `intent==automation` force-routes there (harness.py `_forced_tool`).
- **Wiped 2026-07-31**: `delete_workflow`, `pause_workflow`, `resume_workflow`, `trigger_workflow`, `enqueue_job`, `list_jobs`, `delete_job` removed from `ALL_TOOLS`/executors — redundant duplicates of the automation_* tools (1:1 on the same `workflow_instances` table). `tools/workflow_job_tools.py` keeps only `create_workflow`/`list_available_pipelines`/`list_workflows`. Job-queue management stays worker-internal (UI endpoints `/automations/runs` unchanged).
- **Cloud-first model rule**: builtin handlers MUST default to `clients.cloud_client.DEFAULT_CLOUD_MODEL` (with `raw_completion`'s built-in OpenAI fallback), NOT a hardcoded local `gemma4:12b` — local models aren't reliably loaded in this env and hardcoded ones 404 every run (fixed 2026-07-31 in `jobs/tech_brief_job.py`, `scripts/email_watch.py`, `jobs/handlers/user_automation.py`). Local ollama is a last-resort fallback only, gated behind a cloud-primary try/except.


## Science-scout ledger: local SQLite + Turso replica (2026-10-24)

`store/science_scout_store.py` keeps local `store/science_scout.db` as the **source of truth** and mirrors every write to a Turso (libSQL) database so the claim ledger is readable off-machine.

- **Database**: `libsql://science-scout-intelogroup.aws-us-east-2.turso.io` (org `intelogroup`, group `default`, Free plan). `TURSO_DATABASE_URL` lives in `tools-harness/.env`; `TURSO_AUTH_TOKEN` is a DB token in the Keychain as `clixen.secret.TURSO_AUTH_TOKEN` (listed in `env_secrets.SENSITIVE_KEYS`, so it loads at boot).
- **Transport**: `store/turso_client.py` speaks Hrana-over-HTTP (`POST /v2/pipeline`) with `requests` — deliberately no `libsql-client` dependency. `execute`/`query`/`execute_batch` raise `TursoError`; `mirror()` never raises.
- **Write path**: each store write commits locally first, then `_mirror(sql, params)` replicates the same statement. SQL lives in `_SQL_*` constants used by *both* sides so they cannot drift. Set `TURSO_DISABLE=1` for local-only mode.
- **Failure isolation**: a Turso outage must never break a scan. `mirror()` returns False on any error; after `TURSO_FAIL_THRESHOLD` (3) consecutive failures the circuit opens for `TURSO_COOLDOWN_S` (120s) so an offline machine doesn't pay a socket timeout per write.
- **Backfill**: `scripts/turso_migrate_science_scout.py` (`--dry-run`, `--verify-only`, `--table`, `--batch-size`, `--truncate --yes`). Idempotent `INSERT OR REPLACE`, explicit ids preserved on the AUTOINCREMENT tables; verification compares a canonical SHA-256 per table plus row counts. 2026-10-24 baseline: all 8 tables matched (26,754 rows: 1,133 meta / 2,109 query_usage / 957 niche_perf / 7,548 raw_papers / 6,290 claims / 6,450 links / 2,267 suppressed_alerts).
- **Gotchas**: (1) one round trip per multi-row `INSERT ... VALUES (..),(..)` — 250 single-row statements measured ~13 s vs <1 s for the multi-row form, and the script caps rows per statement by SQLite's ~32k variable limit; (2) `tests/conftest.py` has an autouse fixture setting `TURSO_DISABLE=1` so the suite never writes to the live replica; (3) like every store edit, the running `com.clixen.task_worker` must be restarted (`launchctl kickstart -k`) or the daemon keeps executing the pre-mirror module; (4) `paper_claim_links` is append-only with no natural key — a lost mirror write degrades the replica but is never retried, so `--verify-only` is the reconciliation tool.

### Turso wire-format gotchas (cost real debugging time 2026-10-24)
- **Integers are strings, floats are numbers.** Hrana's typed values are asymmetric: `{"type":"integer","value":"<i64 as string>"}` (JSON numbers lose 64-bit precision) but `{"type":"float","value":<f64 as JSON number>}`. Sending the float as a string is rejected with `JSON parse error: invalid type: string "…", expected f64` and fails the *whole* batch. Only surfaced on the first REAL column (confirmations.db `created_at`).
- **REAL is not bit-identical across engines.** The same value reads back **1 ULP** off from Turso (`1786588681.3877919` vs `.387792`, ~2×10⁻⁷ s on epoch timestamps) even though `printf('%.17g')`/`typeof()` agree. Verification therefore normalizes floats to 12 significant digits before hashing; do not "fix" a REAL mismatch below that precision — it is not data loss.
- **Live/capped tables race with the daemon.** `workflow_state__workflow_instances` (updated every few minutes) and `event_log__events` (capped at 2,000, oldest evicted) will mismatch a snapshot taken minutes earlier — counts can match while content drifts. Re-export with `--truncate --yes` for an exact cut; a non-truncated re-export leaves evicted rows behind.

## Full local-store export to Turso (2026-10-24)

`scripts/turso_export_all.py` snapshots every other local SQLite store into the same Turso database, namespaced `<db>__<table>` (colliding names like `meta`, `query_usage`, `subagent_prompts` exist in several stores; Turso's free plan also caps database count).

- **Sources** (21 DBs, ~25k rows): `store/*.db`, `data/*.sqlite`, `jobs/*.db`, `reminders.db`, `tools/_tool_failures.db`. `store/science_scout.db` is skipped (already mirrored live) unless `--include-science-scout`.
- **Excluded**: `data/tsdata/catalog.db` — a time-series catalog with 2,747,469 `observations` rows. Bulk measurements, not agent state; pass `--db data/tsdata/catalog.db` explicitly if ever needed.
- **Schema generation**: reuses each local object's `sqlite_master.sql`, rewritten to the namespace (`CREATE TABLE IF NOT EXISTS <ns><table>`, indexes, `REFERENCES` retargeted). Unmappable objects (virtual tables, triggers) are skipped and reported.
- **Manifest**: `_export_manifest(source_db, source_table, target_table, rows, sha256, exported_at)` records what was copied and the canonical hash — the drift-check ledger.
- **Verify**: `--verify-only` re-hashes local vs remote per table (float-normalized), `RESULT:` line is the pass/fail gate (exit 1 on mismatch).
- **This is a snapshot, not dual-write.** `world_monitor.db`, `reddit_intel.db` and science scout write through live (see below); every other source needs a re-run to refresh (`--truncate --yes` for capped tables).

## Connection-level mirroring for the other scouts (2026-10-24)

`world_monitor_store` and `reddit_intel_store` dual-write too, but **without per-function wiring**: `_conn()` returns `dbclose.connect_mirrored(path, "<namespace>__")`, and `dbclose.MirroredConnection` intercepts `execute`/`executemany`, buffers every INSERT/UPDATE/DELETE, and flushes the batch to Turso **after** a successful commit. Science scout keeps its explicit `_mirror()` calls (already committed and proven); the connection layer is the pattern for any further store.

- **Namespace matches the export**: targets are `world_monitor__<table>` / `reddit_intel__<table>`, exactly what `scripts/turso_export_all.py` created — so the snapshot and the live stream converge in one table, no migration needed when switching a store on.
- **Transactional**: rolled-back transactions clear the buffer and are never replicated; one commit = one HTTP round trip (`turso_client.mirror_batch`).
- **Best-effort**: a Turso failure logs and drops the batch — the local commit already landed and stays authoritative.
- **DDL never mirrors**: `executescript()` (schema init) bypasses `execute()`, and CREATE/PRAGMA/SELECT don't match the write regex.
- **CPython gotcha that cost a test failure**: `Connection.__exit__` commits through the C API, so it does **not** call a Python-level `commit()` override. `MirroredConnection.__exit__` flushes explicitly; without that, every store using `with _conn() as conn:` silently mirrored *nothing*.
- **Limits**: only `execute`/`executemany` are intercepted — a `cursor()`-based caller bypasses the mirror. Both stores verified `conn.execute`-only before enabling.
- **Tests**: `tests/test_mirrored_connection.py` (buffer-on-commit, rollback discard, namespacing, DDL/SELECT exclusion, mirror-failure isolation, plus a real `world_monitor_store.save_raw_scan` round trip).



