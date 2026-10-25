# 0003 — The reconciler stops, never starts

Status: accepted (2026-10-25). Related: `store/step_journal.py`, `sidecar.rs`.

## Context

Two components can end something's life without asking:

- `clixen-orb`'s sidecar supervisor restarts a dead or hung core (up to 5 times in a
  120 s window).
- The step journal (`store/step_journal.py`) records one leg of a run so a crash is
  recoverable.

Neither has a "start" counterpart, and that asymmetry is deliberate. Copied from the
cloud repo's ADR 0020, whose reasoning applies unchanged at desktop scale:

1. **Destruction is bounded; starting is not.** A wrongful restart of a *process* is
   cheap and visible. A wrongful restart of a *run* spends money, re-executes side
   effects, and can put a second executor next to a live claim.
2. **A failed run is a visible state.** `failed` with a reason is something a human
   can see and retry deliberately.
3. **Starting is a deployment concern.** The process comes back through launchd, not
   through a reconciler guessing.

## Decision

The supervisor may restart the **process**. Nothing may silently resume a **turn**.

A crashed turn leaves a partially-filled step journal (`store/step_journal.py`,
keyed by `(run_id, step_index)`, first-writer-wins, JSON-only payloads). Resuming is
an explicit act: the journal is the replay door, and a future resume path must read
it deliberately — never as a background reconciliation.

The step seam is named but no engine is adopted. Data crosses it as plain JSON
dicts, the shape a durable-execution engine would checkpoint, so adopting one later
is a bounded change rather than an excavation.

## Consequences

- "It restarted itself" is never an explanation the product has to give.
- Long-running capability is bounded by what is journaled, and today that is tool
  calls inside a run — plan status is already durable in `store/run_store.py`.
- A user-visible "resume this task?" affordance is the natural next step, and it is
  an affordance precisely because the system will not do it on its own.
