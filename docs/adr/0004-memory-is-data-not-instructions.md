# 0004 — Memory is data, not instructions

Status: accepted (2026-10-25). Code: `tools-harness/tools/memory_tools.py`,
`tools/injection_guard.py`.

## Context

`remember` is a tool the model can call. A fact it wrote today becomes text in the
prompt tomorrow, in the same font as the system rules — indistinguishable, later,
from an instruction. The cloud repo calls a memory write *"a persistence primitive
for a successful injection"* and ranks write authority just below money.

Three properties were missing locally: history was destroyed by overwrite, `forget`
deleted outright, and recalled memories arrived unfenced.

## Decision

**Version, don't overwrite.** A memory keyed by `remember(..., key="user.city")`
supersedes the live version: the old row keeps `valid_to` and `superseded_by` and
stops being recalled. History is append-only.

**Tombstone, don't delete.** `forget` sets `tombstoned` + `valid_to`. The memory
leaves every future recall immediately; the row survives for audit. Erasing the
record of a poisoned memory destroys the evidence that it existed.

**Fence the recall.** `recall_block()` output is wrapped by the shared injection
fence as `source="memory_recall"`. Memories are text that entered the system outside
the current turn, so they sit below instructions — and the fence is unforgeable, so
a memory containing `</external_content>` cannot break out.

Only live versions with `confidence >= 0.4` are recalled; `tier="always"` still
bypasses the similarity gate but not the fence.

Metadata rides in the row's otherwise-unused `url` column as JSON, because
`KnowledgeBase` drops and recreates its table on any field mismatch — a new column
would wipe every stored memory.

## Consequences

- A corrected or withdrawn fact stops influencing answers without losing the trail.
- Recall failures are loud: one warning per process plus a boot preflight line
  (`preflight: memory: ok (36 live facts, openai embeddings)`). Previously `""` was
  returned for everything, which is indistinguishable from "nothing relevant".
- The agent still writes memories through a tool. That is a deliberate local
  trade-off (single user, no separate write API); the fence and the tombstone are
  what keep it from becoming an instruction channel.
