# Changelog

All notable changes to Clixen are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]
### Changed
- Renamed project Gemma4Llama → **Clixen**.
- Repo declutter: dev/eval scripts and benchmark results moved out of the app top level.

### Removed
- Dead test scaffolding (medicospira browser-automation experiment).
- Dead code sweep: finalized the removed multi-backend search subsystem (orphaned
  `tools/` search modules + `agents/search_graph*` + `search_nodes/` and their tests), the
  `gmail-mcp/` dir, and orphan modules (`local_agent_v2`, `email_sender`, `intent_ranking`,
  `system_notifier`, `workflow_capabilities`). Excised the broken `G4L_LOCAL_AGENT_V2`
  (smolagents v2) branches in `harness.py` that called an undefined function. Untracked the
  runtime `jobs/.cache/`.

### Added
- Persistent cross-session memory (`remember`/`forget` tools + per-turn recall).
- Same-model tool-contention gate (serializes model-invoking tools on the single GPU).

## Earlier
Highlights from prior history (see `git log` for full detail):
- `convert_audio` tool + zero-LLM audio specialist.
- Zero-LLM form/path/read specialists (eliminated per-call LLM overhead).
- Centralized rotating-file logging; temp-file leak fixes.
- 20 native macOS + local-archive tools across 3 tiers.
- PDF form detection and filling tools.
- SSRF guard on URL fetch.
- Unified DB-driven workflow/automation system with agent CRUD tools.
