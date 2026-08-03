# Local-Agent Toolset

Toolset is **task-scoped** (`agents/local_agent_tools.py`):
- `task="document"` → core tools only (filesystem + doc creation) — fastest, smallest prompt
- `task="code"` → core + code tools (grep, file_tree, parse_code, edit_file_fuzzy, **plus subagent tools**)
- `task="full"` (default) → core + form + code tools — everything

The code task additionally includes orchestrator subagent tools so the coding agent can delegate:
```
ask_dev_agent        # git writes (commit/push/merge), REPL inspection, process queries
ask_research_agent   # library docs, API refs, Wikipedia/arXiv/PubMed, sports scores
ask_web_search        # internet search for solutions, Stack Overflow, docs, news
```
These are the same subagents the orchestrator calls; from the local agent they run as
normal tools (synchronous, result returned to the agent loop). They are how the code agent
does git writes — `bash_exec` blocks git write ops (commit/push/merge/reset --hard).

Core/local-agent tools (always present in full/code):
```
read_file, read_document, read_pdf          # read
write_file, edit_file, create_directory     # write
list_directory, find_files, bash_exec       # inspect + shell
detect_form_fields, fill_form, update_form  # PDF/DOCX forms
detect_pdf_form_fields, fill_pdf_form       # PDF-only forms
vision_detect_form_fields, vision_fill_form_fields  # scanned PDF forms
detect_flat_pdf_fields, fill_flat_pdf       # flat PDF (CIDFont) forms
data_to_xlsx, markdown_to_pptx             # document creation
transcribe_audio                            # audio transcription
```

Code-task-only tools (present in code/full):
```
grep_files, file_tree, parse_code, edit_file_fuzzy, run_python, debug_py   # code introspection/run
```

To add a tool: tag it in `TOOL_TAGS` (in `tools/registry.py`) with at least one of
`core` / `form` / `code` — `_CORE_TOOL_NAMES` / `_FORM_TOOL_NAMES` / `_CODE_TOOL_NAMES`
are derived from those tags via `tools_with_tags()`. The tool must also exist in
`registry.py` EXECUTORS. Do NOT edit `_LOCAL_AGENT_TOOL_NAMES` directly (it is computed).

## Registry Tools — Full Inventory

All 100+ tools defined in `tools/registry.py`:
- `EXECUTORS` dict (line 768) maps tool name → lambda
- `ALL_TOOLS` list (line 575) collects all schemas
- Tools reachable via intent routing + standard LLM tool loop
- Tools exclusively via skills hub: Google Docs/Sheets, clutter fixer, YouTube, video/audio processing, WhatsApp send, research (arXiv/PubMed)

## Form Workflow Pattern

```
1. detect_form_fields(path)          → returns field names/types/values
2. fill_form(path, fields={...})     → fills values, saves _filled.pdf
3. detect_form_fields(filled_path)   → verify values stuck
4. confirm to user
```

Pipeline hints in `local_agent_nodes.py:316-355` enforce this order. The verification step (step 3) is critical — added to `fill_form` hint.

## Pipeline Hint Rules

When adding hints to `execute_tool()`:
- Use forceful language: "NOW call..." not "Next step:..."
- Always truncate detect results to ≤10 fields (`> 10` check)
- JSON examples must use `{{"field": "value"}}` (double braces for `.format()`)
- Check tool availability: `"fill_form" in executors` before adding hint

## System Prompts

Single dynamic builder in `local_agent_nodes.py` — no per-model prompt variants anymore:
- `_build_system_prompt(task, tool_schemas, home)` (line 40) — composes the prompt per task (`document`/`code`/`full`) and the actual tool schemas passed in.
- `_get_system_prompt(model, task="full", query="", tools=None)` (line 207) — wraps `_build_system_prompt`, prepends a memory-recall block via `tools.memory_tools.recall_block(query)` when a query is given.

## Path Resolution

`_resolve_form_path()` / `_resolve_pdf_path()` in `form_tools.py` / `pdf_tools.py`:
1. `Path.expanduser()` — handles literal `~`
2. If not found and starts with `/home/` → substitute real home dir
3. `.resolve()` — canonical path

All form functions (`detect_*`, `fill_*`, `update_*`) route through these helpers.
