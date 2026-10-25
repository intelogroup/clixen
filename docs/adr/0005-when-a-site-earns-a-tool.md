# 0005 — When a site earns a tool

Status: accepted (2026-10-25). Code: `tools/screen_v2.py` (generic layer),
`tools/wikipedia_refs.py` (an adapter), `soak/tool_demand.py` (the gate).

## Context

Asked whether the orb should carry a tool or skill per site (web, Chrome, Safari,
Wikipedia, "any common site"), the evidence said no:

- Of 7 520 logged tool calls, site-specific tools accounted for ~0.6%
  (`slack_search` 18, `search_gdelt_news` 27, all YouTube tools 26, GitHub/Jira/
  Notion/arXiv 0). The volume is in generic primitives (`searxng_search` 563,
  `observe` 172, `click_text` 174).
- The orb's prompt is at 3 196 / 3 200 chars, and every tool adds 450–900 chars of
  schema that is charged on **every** round. Twenty site tools would cost 1.5–2.5×
  the entire current tool block.
- The two failures that actually hurt were generic: a missing parser (citations)
  and an ambiguous browser instance (two Chrome profiles), not a missing site tool.

## Decision

**Three tiers.**

1. **Generic primitives** — `open_url`, `tabs`, `read_page_text`, `observe`, `act`,
   `window`, `app`. A site is a *target*, a browser is an `app=` argument. There is
   never a `chrome_*` / `safari_*` tool family; Safari support lives inside `tabs`
   as a different AppleScript dictionary, not as a second tool.
2. **Data adapters** — a real tool only when the site exposes **structured data**
   the screen cannot give cheaply or correctly, *and* usage justifies the schema.
   `wikipedia_references` qualifies (316 ms, correct, auditable) where
   `ask_research_agent` took 75 s and answered wrongly.
3. **Recipes and skills** — procedures with zero schema cost. `app_task` recipes
   (`gmail.send`, `web.read`) today; skill documents read on demand next.

**Promotion rule, as a number:** a site-specific capability becomes a tool at
**≥20 calls**, with structured data and a stable API, as measured by
`soak/tool_demand.py` — never by estimate or enthusiasm. Below the threshold it
stays a skill or a `read_page_text`.

## Consequences

- Adding a site tool now requires a measurement to point at.
- A site that breaks stays fixable in one place (the generic layer) instead of N
  tool implementations.
- The visual path (AX + `act`) remains the default even where an API exists, so the
  user keeps watching the bunny do the work; adapters are for what pixels cannot
  answer reliably (dates, ids, counts).
