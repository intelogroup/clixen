# Skills Hub Matching (`skills_hub.py`)

`_score_skill()` scores a skill against a user query via 4 signals:

1. **Exact phrase match** (`kw_lower in query_lower`) — multi-word triggers score 2.0, single-word 1.0. Weak keywords (show/get/the/a) score 0.3.
2. **Token overlap** (ponytail: added 2026-07) — if exact phrase doesn't match, checks per-word overlap between query and trigger keywords. Weak keywords filtered out. Score 0.5 per trigger keyword sharing ≥1 non-weak token. Catches: "test website" → "test the site" (via "test").
3. **Description token overlap** (ponytail: added 2026-07) — same overlap check against the skill's full description. Uses `_stem()` for basic suffix stripping (ies→y, ing, ed, ly, es, s). Punctuation stripped. Score 0.3 per overlapping stemmed token. Catches: "website has visual inconsistencies" → design-review (via "visual" trig + "inconsistency" desc).
4. **Domain anchor** — regex patterns for email/calendar/tasks/search/browser/docs/git/code/telegram/admin/monitor/research/transit/business/knowledge/communication. Score 3.0 if query matches a domain AND skill category aligns.

Blockers:
- **Round penalty**: short queries (<15 chars) on complex skills (max_rounds>6) lose 1.0. Prevented vague queries from matching multi-step skills; threshold lowered 30→15 chars 2026-07 since real queries like "review the design" (17 chars) are specific enough.
- **`_WEAK_KEYWORDS`** (22 words): common/bleeding words filtered from token overlap. Set: `today`, `tomorrow`, `now`, `this`, `my`, `show`, `get`, `the`, `a`, `an`, `what`, `when`, `how`, `is`, `are`, `can`, `will`, `create`, `list`.

## `_stem()` ponytail stemmer

Strips common English suffixes for rougher matching. Only applied to description overlap (not triggers — triggers are curated, descriptions aren't). Rules: ies→y, ying→y, ing→, ed→, ly→, es→, s→. Words <5 chars pass through unchanged.

## Dispatch hierarchy

`run_skill(id, message)`:
1. External skills (ext.*) → `load_external_skill()` → reads raw SKILL.md, feeds instructions to orchestrator sub-agent with full toolset.
2. Single-tool skills → extracts params from JSON schema, calls executor directly (no LLM).
3. Multi-tool skills → `harness.run()` with skill's tools/model/prompt/rounds.

`load_external_skill(path, message)` — FALLBACK for unregistered skills only. `run_skill` is the preferred path for registered skills.
