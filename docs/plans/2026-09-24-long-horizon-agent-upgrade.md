---
kind: plan
subject: clixen long-horizon local agent (resumable runs, process spawning, nested planning)
status: in_review
created: 2026-09-24
---

# Long-Horizon Local Agent Upgrade — Top-Level Plan

## Grounding — what already exists

| Piece | File | Role today |
|---|---|---|
| One-shot job queue | `jobs/job_queue.py` | SQLite, `claim_next()`, checkpoints |
| Scheduled automations | `store/workflow_store.py` | APScheduler CronTrigger, IANA timezones |
| Worker poll loop | `jobs/worker.py` | 10s interval, 17 handlers, crash-proof `except Exception`; `claim_next()` uses `BEGIN IMMEDIATE` atomic claims and **already has `reap_stale_running(600s)`** |
| Per-run plan state | `store/plan_store.py` | **already persists per-run plans** (steps + done, 200-run cap, dbclose pattern) — WS3 extends, does not build |
| Tool loop | `clients/cloud_client._run_tool_loop` (cloud_client.py:810), `ollama_client.chat`, `local_agent_nodes.tool_node` | bounded rounds, 3-error bailout; `_run_tool_loop` **already accepts `run_id`** and stamps `trace_store` |
| Orchestrator + mailbox | `tools/orchestrator_tools.py`, `jobs/agent_message_job.py` | 22 subagent tools, agent→agent messages |
| Budgets / traces | `clients/cost_guard.py`, `trace_store`, `event_log` | daily token cap, fcntl lock, activity log |
| Conversation persistence | `store/conversation.py` | sliding window + continuous-fold compaction |

The gap is not "add a queue" — it is making the agent loop itself a durable, resumable, spawnable object.

## WS1 — Resumable run sessions (core)

- Extract the tool loop into a first-class **Run** backed by an append-only **event journal** (`store/run_store.py`, SQLite WAL, `store/dbclose.connect` pattern like `plan_store`): `user_msg / assistant_msg / tool_call / tool_result / plan_step / heartbeat / budget` events keyed by `run_id`.
- **Round-boundary snapshot semantics** (grounded in `clients/cloud_client.py:830–844`): after each completed round, append a `round` event carrying the full message list (or delta + hash), `escalated` flag, active model/tier, `consecutive_errors`, and whether `force_tool_choice` was consumed. Resume restores ALL of it. An in-flight API call is never resumed — its round restarts from its boundary. This keeps the stateless-loop invariant true across the fallback cascade (whose messages are already-mutated on retry, cloud_client.py:842).
- **Run-id authority**: the journal's `run_id` is the single source of truth. `CURRENT_RUN_ID` (log_config) stays **logging-only** — the ContextVar is stamped at run start for log correlation, but it is not the data path: `trace_store` / `plan_store` / journal writes all pass the run id **explicitly**, and a contract test asserts all three ids match per run. M1 delta (outside-voice finding): `_run_tool_loop` currently stamps `run_id` only on the forced-tool path (cloud_client.py:866–873) — every main-loop round/trace/budget/abort event gains the stamp.
- Loop becomes stateless: `load journal → one model round → append → repeat`. Crash/restart = resume from last event. Tool results cached by `tool_call_id`.
- Chat returns immediately with `run_id`; UI/Telegram subscribes to SSE event stream. 300s request bound replaced by per-run policy (deadline, max rounds, cost — checked every round via `cost_guard`).
- **Tool idempotency classes**: `readonly` / `replayable` / `side-effecting`. Side-effecting calls go through approval gate (existing approvals store) or dedup keys.
- **Journal write-boundary redaction**: every `tool_result` passes a secret-scrubber before append — exact-match against all vault-known values plus keyname regex (`password|token|authorization|secret|api[_-]?key`). Scrubbed results store a `[REDACTED:type]` marker; on resume, a redacted `side-effecting` call is **re-executed** (not replayed from journal) and a redacted `readonly` result triggers refetch. The journal never contains a replayable secret.

## WS2 — Process supervisor & spawning

```
launchd (com.clixen.core)
 └── worker supervisor (jobs/worker.py, task_worker thread)
      │  claim_next() → LEASE + heartbeat (visibility timeout)
      ├── spawn ──► child process: python -m jobs.run_child --run-id R1
      │              └── harness tool loop (rounds; budget check EVERY round)
      │                    ├─ append events ──► SQLite run journal (WAL,
      │                    │                     single writer = child;
      │                    │                     supervisor = reader/reap)
      │                    ├─ side-effect tools ► approval gate / dedup key
      │                    └─ agent-mailbox ◄──► sibling children / orchestrator
      ├── spawn ──► child process: --run-id R2 (parallel runs isolated)
      │
      ├─ RESTART BOUNDARY: supervisor dies → launchd restarts →
      │   leases expire → heartbeat misses → orphan children REAPED
      │   (SIGTERM → SIGKILL; never reattached) → fresh child resumes
      │   from journal (one-writer invariant holds, no run lost)
      └─ SSE fan-out: journal tail ─► chat/Telegram run surface (WS4)
```

- **What already exists (do not rebuild)**: `job_queue.claim_next()` already does `BEGIN IMMEDIATE` atomic claims, and `reap_stale_running(600s)` already requeues wedged jobs. WS2's genuine delta: add lease expiry + heartbeat updates on running jobs, per-run isolation metadata, and child spawn on top of the existing claim/reap substrate.
- Each run executes in a **spawned child process** (`python -m jobs.run_child --run-id …`): crash/OOM/timeout isolation, per-job kill; parent supervises and resumes from journal.
- `spawn_agent(run_id, toolset, sandbox)` primitive: child harness with restricted tool registry (FS root via existing `tools/filesystem` ContextVar; network allowlist; optional Seatbelt profile), communicating via agent-mailbox — parallel subagents running for hours, fully local.
- **Orphan policy — reap, never adopt.** On supervisor restart: any child whose lease/heartbeat expired gets SIGTERM (SIGKILL after grace); the run resumes in a fresh child from the journal. Children are never reattached (no dual-writer risk, no IPC adoption protocol). A live heartbeat gap counts as one retry attempt per the interaction state table.

## WS3 — Long-horizon planning (nested loops)

- Promote the existing **mid-run plan checkpoint** to first-class plan state: `store/plan_store.py` already stores per-run `steps` + `done` — WS3's delta is (a) statuses beyond done (`in_progress`, `blocked`), (b) writing plan transitions as journal `plan_step` events, (c) resume rehydrates plan from the journal rather than from plan_store alone. Scope boundary (outside-voice finding): `plan_store` is *extended, not rebuilt*; the **event journal itself remains the new `run_store`** — plan_store's `(run_id, steps, done, updated_at)` schema is not an event log and will not become one.
- Outer loop = plan traversal (spans days, re-plans on failure); inner loop = WS1 tool rounds. M1 ships crash-**resumable** state (journal survives, restore is manual/on next activation); M3 makes it crash-**resuming** (supervisor auto-reaps and re-spawns). M1 never claims automatic resume.
- Context bounded across days via existing continuous-fold compaction.
- Recurring/catch-up scheduling rides on `workflow_store`; add missed-run backfill + per-run journals.

## WS4 — Control surface & observability

- Live run timeline UI (journal → SSE): rounds, tool calls, browser screenshots, tokens/$.
- Pause / resume / kill / steer (inject user message into journal mid-run).
- `trace_store` keyed by `run_id`; per-run cost rollups on `cost_guard`.

## Accessibility contract (M6 exit criteria)

- **Status = color + icon + text**, never color alone (pill renders `● Running` / `✕ Failed` / `⏸ Paused` with text label).
- **Timeline is an `aria-live="polite"` region** announcing throttled milestones only — round-complete, step-change, failure — never per-token.
- **Full keyboard path**: every control (pause / resume / kill / steer / expand-round) reachable and operable without a pointer; shortcut hints rendered in control tooltips and documented in the timeline header.
- **Auto-scroll respects `prefers-reduced-motion`** — pinned position + "new events ↓" affordance instead of animated scroll.
- **Journaled screenshots carry alt text** = the plan-step description they were captured under (already a journal field — reuse, don't regenerate).

## Interaction State Table (run surface + journal)

| State | Trigger | Status copy | Affordances | Auto-retry | Resumable? |
|---|---|---|---|---|---|
| **queued / replaying** (loading) | run created, journal loading | `Starting run…` + spinner; replay shows `Restoring step N of M` | cancel only | n/a | n/a |
| **running** | model round executing | `Running — step 3/7: <plan step>` + budget bar | pause, kill, steer | n/a | n/a |
| **zero-events** (empty) | journal exists, no assistant event yet | `Warming up — first model round pending` | kill | rounds auto-advance | yes |
| **paused** (partial) | user pause | `Paused at step 3 — will not advance until resumed` | resume, kill, steer | disabled | yes |
| **steer-waiting** (partial) | run paused awaiting user input/approval | `Waiting for your input (approval: send Telegram)` | submit input/approve, kill | disabled | yes |
| **child-crashed + retrying** (error) | lease expired / OOM / nonzero exit | `Run interrupted — retrying (attempt 2/3) from last checkpoint` + attempt counter | kill | yes, exp backoff ≤3 | yes |
| **failed** (error) | retries exhausted / unreplayable side-effect event | `Failed at step 5: <error> — journal preserved` | resume (manual), view log | no | yes, with replay guard |
| **budget-exceeded** (error) | deadline / cost_guard cap hit | `Stopped — $X or Yh limit reached (journal preserved)` | resume (raises budget) | no | yes |
| **killed** (success-variant, user intent) | user kill | `Killed by you at step 4` | view log, new run | no | yes (explicit resume) |
| **succeeded** | final answer event appended | `Done — <one-line outcome>` | view, rerun | n/a | n/a |

Rule: every error row shows the journal-preserved affordance — a failed run is
never a dead end; only `succeeded` and `killed` hide the resume control.

## Run Surface IA (what the user sees)

```
chat (web/Telegram)
 └─ run card            ← appears instantly on send: status pill + one-line goal
     └─ run timeline    ← PRIMARY: status + live plan/step progress (done/active/blocked)
         │               SECONDARY: expandable round → tool-event stream,
         │               browser screenshots, tool results inline
         └─ round detail ← TERTIARY: tokens/$ (budget bar), model used,
                            controls: pause / resume / kill / steer
```

Hierarchy rule: status + plan progress are never collapsed or scrolled away;
round detail is progressive disclosure. A run that is not active shows its
final state card (succeeded / failed / killed / budget-exceeded) with a
"resume" affordance only when the journal says resumable.

## Milestones (each shippable alone)

| M | Deliverable | Unlocks |
|---|---|---|
| M1 | Run journal + loop extracted + replay-safe tool cache | crash-resume |
| M2 | Async runs: chat → `run_id` + SSE progress, pause/kill | multi-minute tasks |
| M3 | Supervisor + per-run child processes + leases/retries/DLQ | multi-hour tasks, isolation |
| M4 | Persisted plan object + backfill/catch-up | multi-day tasks |
| M5 | `spawn_agent` + restricted toolsets + mailbox fan-out | parallel local multi-agent |
| M6 | Timeline UI, steer, budget dashboards, sandbox hardening | full local "Grok-grade" UX |

## Test strategy (M1)

```
unit        journal append/restore ──► run store (round snapshot fields, seq,
                                       redaction markers, prune cap)
unit        redactor ──► vault values + keyname regex; precision/recall cases
integration crash-injection ──► spawn child, SIGKILL mid-round, resume
                              ⇒ same final answer as uninterrupted baseline
integration idempotency ──► side-effect tool called once across resume
integration concurrency ──► two processes appending one journal (busy_timeout)
contract    parity ──► cloud vs ollama vs local_agent_nodes loop shape
golden      full suite non-regression (round counts, token budgets)
```

Known gaps to build: ~~crash-injection fixture~~ **BUILT 2026-09-24**
(`test_real_crash_midrun_resume_replays_cache` — spawns a child, SIGKILLs it
after its `tool_result` is journaled, resumes in-process, asserts the answered
call is never re-executed and the run still reaches its final answer); a fake
`check_budget` for deterministic budget-stop tests remains. E2E browser-run
idempotency tests reuse the headed-browser persistent profile.

M1 status: `run_store` + `run_loop` implemented (23 tests green); loop extraction
from `cloud_client._run_tool_loop` (the `_LoopState`/`one_round` refactor) and
the live cloud adapter remain.

## Performance budget (M1)

| Metric | Target | Notes |
|---|---|---|
| journal append | <5 ms/round | one INSERT batch per round event |
| resume | <5 s for 1k events | single read + reconstruct |
| SSE tail poll | 250 ms, `Last-Event-ID` resume | monotonic `seq` (mirror `trace_store` atomic-seq pattern) |
| heartbeat | 15 s | well under `reap_stale_running(600s)` |
| journal retention | 500 runs / 30 days + periodic VACUUM | `plan_store` 200-run cap is the precedent |

## Code quality (M1)

- Extract, don't rewrite: `_run_tool_loop` (cloud_client.py:810) stays in place and keeps `chat()`'s fallback cascade as its outer retry. Extraction introduces a small `_LoopState` dataclass (messages, escalated, consecutive_errors, round_idx, force_tool_consumed) and a `one_round(state) -> RoundResult` function; the flat `for _round in range(max_rounds)` body becomes a driver over it. The cloud↔ollama↔local_agent_nodes **parity invariant** (documented in each module) must hold after extraction — no path may gain/ lose budget checks, abort checks, or trace stamps.
- Reuse existing policy hooks: `check_budget()` / `check_aborted()` (already called per round) are where per-run deadline/cost policy attaches — no parallel budget implementation.
- Retention precedent: journal pruning reuses `plan_store._evict_if_over_cap` shape (cap + evict oldest), not a new retention framework.

## NOT in scope (M1)

- Distribution/CI: no new artifact, binary, or package — `run_child` is an in-repo module; nothing to publish. CI remains the existing pytest flow.
- Sandbox/Seatbelt profiles (M5), SSE UI implementation (M2/M6), child spawn (M3) — reviewed at their own milestones.
- UI redesign beyond the run-surface IA committed in the design review.

## Failure modes per new codepath (M1)

| Codepath | Production failure | Plan covers? |
|---|---|---|
| `run_store` append | disk full / locked → round lost mid-run | yes — append before dispatch; failure marks run `failed` w/ journal preserved |
| round snapshot restore | schema drift after upgrade (old journal, new fields) | yes — journal `schema_version` from day one; unreadable journal ⇒ quarantine + explicit restart, never silent replay |
| resume path | restore from a journal whose last round was a side-effecting tool | yes — idempotency classes + replay guard (redacted/side-effecting ⇒ re-execute, not replay) |
| redactor | over-redaction eats legitimate text / under-redaction leaks a secret | yes — precision/recall unit cases; fail-closed on ambiguity (redact + marker) |
| `check_budget` attach | per-run policy bypassed on a new loop path | yes — parity contract test asserts every loop path calls budget/abort checks |
| CURRENT_RUN_ID stamp | logs/traces split across two run-id namespaces | yes — single-authority rule; contract test asserts one id per run |

## Run lifecycle IO contracts

| Feature | Started when (input trigger) | Input | Output | Never does |
|---|---|---|---|---|
| **run start** | `chat-send` \| `automation-enqueue` \| `agent-mail` | goal text + policy (deadline/rounds/cost) | journal `run` event + immediate run card with `run_id` | never blocks the sender waiting for completion |
| **steer** | user submits while running or paused | free-text message | journal `steer_event` + immediate ack (`Steered — applies next round`); consumed at the next round boundary in any non-terminal state; terminal state → rejected with `Run already finished — start a new run` copy | never interrupts a round mid-execution; never mutates already-appended events |
| **approval gate** | side-effecting tool requires authorization | approve / deny (from run surface or Telegram) | approve → journal `approval` event + resume-signal to child loop; deny → journal `denial` event + notify the calling caller/step with denial reason | never auto-approves after timeout (default = deny + journal); never exposes secret payloads in the approval prompt |

## Risks

1. Non-replayable side effects (browser, Telegram dup-sends) → idempotency keys/approval gate in M1.
2. SQLite write contention → one writer per DB, WAL + `busy_timeout` (existing pattern).
3. Cost blowups on open-ended runs → per-run deadline + `cost_guard` hard stop every round.
4. Stale-daemon module trap → journal schema versioning from day one.

## GSTACK REVIEW REPORT

| # | Pass (dimension) | Before → After | Findings | Status |
|---|---|---|---|---|
| 1 | Information Architecture | 5 → 9 | No run-surface hierarchy → Run Surface IA section with ASCII flow + non-active state rule | fixed_in_file |
| 2 | Interaction State Coverage | 4 → 9 | No state table → 10-row loading/empty/error/success/partial table w/ copy, affordances, retry, resumability | fixed_in_file |
| 3 | Visual Language & Hierarchy | 6 → 9 | No topology diagram → supervisor/child/journal/mailbox/SSE diagram with restart boundary | fixed_in_file |
| 4 | Trust & Error States | 7 → 8 | (a) journal persists tool results verbatim → write-boundary redaction policy; (b) "reaped or adopted" orphan ambiguity → reap-never-adopt locked | fixed_in_file |
| 5 | Brand & Voice | 8 | No issues found | pass |
| 6 | Accessibility | 3 → 9 | Zero a11y requirements → Accessibility contract as M6 exit criteria (5 clauses) | fixed_in_file |
| 7 | Completeness (IO-Driven) | 7 → 9 | Triggers/steer/approval lacked IO contracts → Run lifecycle IO contracts table | fixed_in_file |

| Runs | Skill | Status | Findings |
|---|---|---|---|
| 1 (2026-09-24) | plan-design-review | complete — 7/7 passes | 7 fixed, 0 open |
| 2 (2026-09-24) | plan-eng-review (scope: M1) | complete — 4/4 sections | 9 fixed, 0 open |

**Eng-review findings (all fixed in-file):** (1) journal can't reconstruct loop-internal state → round-boundary snapshot semantics; (2) run-id authority split (outside-voice) → explicit-id data path + main-loop stamping delta; (3) plan_store ≠ event journal (outside-voice) → explicit scope boundary; (4) `claim_next`+`reap_stale_running` already exist → WS2 restated as extend; (5) M1 resume trigger undefined → resumable-not-resuming scoping; (6) extract-don't-rewrite with `_LoopState`/`one_round` + parity invariant; (7) test strategy + crash-injection gap; (8) performance budget table; (9) failure-modes-per-codepath table; NOT-in-scope written.

CODEX VERDICT: **SKIPPED_UNAVAILABLE** (401). CROSS-MODEL VERDICT: **ABSORBED** — `claude -p` consult ran (code-grounded, 4 findings); #1/#3/#4 folded into WS1/WS3, #2 validated the existing WS2 delta.

**VERDICT: APPROVED FOR BUILD** — M1 architecture is code-grounded (cloud_client.py:810 loop, job_queue claim/reap, plan_store schema, log_config ContextVar all verified); eng-review gate cleared at commit `177d2dd`.

NO UNRESOLVED DECISIONS
