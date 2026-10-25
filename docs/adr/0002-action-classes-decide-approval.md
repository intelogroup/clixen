# 0002 — Action classes decide approval, never the model's intent

Status: accepted (2026-10-25). Code: `tools-harness/tools/action_class.py`,
`tools/registry.py`.

## Context

The orb's system prompt carries rule 6: *irreversible actions (delete, payment,
permissions, installers) need the user to have asked for exactly that*. That is
prose. It holds only as far as the model follows it, and nothing in the runtime can
tell whether a given call was a read or a payroll transfer.

The cloud repo solved the same problem structurally: every capability declares an
`ActionClass` (READ / WRITE / MONEY) up front, and the class decides approval
behaviour — the model cannot upgrade a write to a read by describing it that way.

## Decision

Every tool has a declared class. Explicit for the tools where a mistake is
expensive; verb heuristics otherwise, and the heuristics fail **safe** (an unknown
`send_*` / `delete_*` / `transfer_*` is a write or money, never a read).

`MONEY` always parks for a human. `WRITE` parks when `CLIXEN_APPROVALS=1`. Both go
through the existing durable confirmation gate (`tools/confirmation.py`), so an
approval is a single-use token with a TTL, not a boolean.

Approvals are **off by default** for one honest reason: the orb has no approval UI
yet, so parking every write would leave calls stuck at `[awaiting confirmation]`
forever — worse than the prompt rule it replaces. Classification and the audit line
run either way, so switching enforcement on is a flag, not a rewrite.

## Consequences

- The audit trail now says *what kind of act* happened, not just which tool ran.
- A new tool gets a class by default through the heuristics; a wrong classification
  is one line to fix and a test to pin.
- Until the approval UI exists, WRITE enforcement is a deployment choice. That gap
  is visible in `soak/capability_matrix.py` under Tool governance rather than
  assumed away.
