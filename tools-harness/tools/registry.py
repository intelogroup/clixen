"""
Central registry of all tool schemas and executors.
Add new tools here — harness.py imports from this file only.

Split across tools/_registry/{imports,schemas,executors}.py to keep this file small;
this module re-exports everything so `from tools.registry import X` is unchanged.
"""

from __future__ import annotations

import logging
import re as _re
import shlex

import jsonschema

log = logging.getLogger(__name__)

from tools._registry import imports as _ri
globals().update({k: v for k, v in vars(_ri).items() if not k.startswith("__")})

from tools._registry.schemas import ALL_TOOLS, AGENT_TOOLS, PLAN_TOOLS
from tools import tool_failure_log

# Built once from the same schemas already used to advertise tools to the model —
# no separate schema-authoring needed, these are real JSON Schema `parameters` blocks.
_PARAM_SCHEMAS: dict[str, dict] = {
    t["function"]["name"]: t["function"]["parameters"] for t in ALL_TOOLS
}

PLAN_MODE_ACTIVE = False

# Tools blocked in Plan mode
PLAN_BLOCKED_TOOLS = {
    "write_file", "rename_file", "delete_file", "create_directory", "copy_file",
    "git_add", "git_commit", "git_checkout", "git_new_worktree",
    "bash_exec", "edit_file", "append_file", "download_url",
    "run_inbox_monitor", "convert_pdf", "fill_pdf_form", "fill_form", "update_form",
    "vision_fill_form_fields", "vision_update_form_fields",
    "send_telegram", "send_email",
    "create_calendar_event", "delete_calendar_event",
    "create_task", "complete_task", "delete_task",
    "set_reminder",
    "run_python", "reset_kernel",
}

# Tag names mirror the hand-maintained tool-subset variables they replace (harness.py's
# _FS_TOOL_NAMES etc., local_agent_tools.py's _CORE_TOOL_NAMES/_FORM_TOOL_NAMES, and each
# agents/specialists/*_specialist.py's own *_TOOL_NAMES — "spec_*" tags). One tool can carry
# multiple tags. This is the single source those consumers now derive from via
# tools_with_tags() instead of each hand-duplicating a subset of EXECUTORS by name.
TOOL_TAGS: dict[str, frozenset[str]] = {
    "analyze_dataset": frozenset({"spec_data"}),
    "api_config_from_curl": frozenset({"browser"}),
    "api_discover": frozenset({"browser"}),
    "api_fetch": frozenset({"browser"}),
    "api_list_domains": frozenset({"browser"}),
    "append_file": frozenset({"code", "ide_extra", "spec_write"}),
    "download_url": frozenset({"core", "fs", "ide_extra"}),
    "archive_grep": frozenset({"fs"}),
    "bash_exec": frozenset({"core", "git", "ide_extra", "repl"}),
    "browser_check": frozenset({"browser"}),
    "browser_click": frozenset({"browser", "spec_scraper"}),
    "browser_close": frozenset({"browser"}),
    "browser_get_attribute": frozenset({"browser", "spec_scraper"}),
    "browser_get_content": frozenset({"browser", "spec_scraper"}),
    "browser_get_url": frozenset({"browser", "spec_scraper"}),
    "browser_load_session": frozenset({"browser"}),
    "browser_navigate": frozenset({"browser", "spec_scraper"}),
    "browser_run_js": frozenset({"browser", "spec_scraper"}),
    "browser_save_session": frozenset({"browser"}),
    "browser_screenshot": frozenset({"browser", "spec_scraper"}),
    "browser_select": frozenset({"browser", "spec_scraper"}),
    "browser_snapshot": frozenset({"browser", "spec_scraper"}),
    "browser_type": frozenset({"browser", "spec_scraper"}),
    "browser_wait": frozenset({"browser", "spec_scraper"}),
    "bus_eta": frozenset({"spec_transport", "transit"}),
    "make_phone_call": frozenset({"spec_phone_call", "utility"}),
    "convert_audio": frozenset({"spec_audio"}),
    "copy_file": frozenset({"code", "fs", "spec_write"}),
    "correlate": frozenset({"spec_data"}),
    "create_directory": frozenset({"core", "fs", "spec_write"}),
    "create_pdf": frozenset({"core", "fs"}),
    "data_to_xlsx": frozenset({"core", "fs", "spec_write"}),
    "delete_file": frozenset({"core", "fs", "spec_write"}),
    "detect_flat_pdf_fields": frozenset({"form"}),
    "detect_form_fields": frozenset({"form", "fs", "spec_form"}),
    "detect_outliers": frozenset({"spec_data"}),
    "detect_pdf_form_fields": frozenset({"form", "fs", "spec_form"}),
    "download_video": frozenset({"spec_video"}),
    "edit_file": frozenset({"core", "fs", "git", "ide_extra", "spec_write"}),
    "edit_file_fuzzy": frozenset({"code", "ide_extra", "spec_write"}),
    "call_my_phone": frozenset({"core"}),
    "get_my_doordash_orders": frozenset({"browser"}),
    "get_my_doordash_cart": frozenset({"browser"}),
    "get_doordash_order_status": frozenset({"browser"}),
    "get_my_uber_trips": frozenset({"browser"}),
    "estimate_uber_ride": frozenset({"browser"}),
    "fd_find": frozenset({"spec_path"}),
    "file_info": frozenset({"code", "fs"}),
    "file_tree": frozenset({"code", "fs", "spec_path"}),
    "fill_flat_pdf": frozenset({"form"}),
    "fill_form": frozenset({"form", "fs", "spec_form"}),
    "fill_pdf_form": frozenset({"form", "fs", "spec_form"}),
    "find_files": frozenset({"core", "fs", "spec_audio", "spec_path", "spec_video"}),
    "find_largest": frozenset({"fs"}),
    "find_recent": frozenset({"fs"}),
    "forget": frozenset({"core"}),
    "get_youtube_transcript": frozenset({"spec_video"}),
    "github_search": frozenset({"core"}),
    "github_list_prs": frozenset({"core", "git"}),
    "github_create_issue": frozenset({"core", "git"}),
    "github_create_pr": frozenset({"core", "git"}),
    "git_add": frozenset({"git"}),
    "git_checkout": frozenset({"git"}),
    "git_commit": frozenset({"git"}),
    "git_diff": frozenset({"git", "ide_extra", "code"}),
    "git_log": frozenset({"git", "code"}),
    "git_new_worktree": frozenset({"git"}),
    "git_status": frozenset({"git", "ide_extra", "code"}),
    "glob": frozenset({"spec_path"}),
    "grep_files": frozenset({"code", "fs", "spec_path"}),
    "html_to_file": frozenset({"fs", "spec_write"}),
    "index_directory": frozenset({"fs"}),
    "json_to_docx": frozenset({"core", "fs", "spec_write"}),
    "list_directory": frozenset({"core", "fs", "spec_audio", "spec_path"}),
    "list_email_attachments": frozenset({"fs"}),
    "list_kernel_vars": frozenset({"repl"}),
    "load_external_skill": frozenset({"core", "fs"}),
    "list_windows": frozenset({"ide_extra"}),
    "make_summary_table": frozenset({"spec_data"}),
    "markdown_to_docx": frozenset({"core", "fs", "spec_write"}),
    "markdown_to_pdf": frozenset({"core", "fs", "spec_write"}),
    "markdown_to_pptx": frozenset({"core", "fs", "spec_write"}),
    "local_vision_highlight": frozenset({"browser"}),
    "local_vision_snap": frozenset({"browser"}),
    "render_diagram": frozenset({"core", "spec_write"}),
    "edit_image": frozenset({"core", "fs", "spec_write"}),
    "parse_code": frozenset({"code", "fs", "spec_read"}),
    "parse_document": frozenset({"fs"}),
    "parse_email_file": frozenset({"fs"}),
    "parse_file": frozenset({"fs", "spec_data", "spec_read"}),
    "plot_chart": frozenset({"spec_data"}),
    "plot_interactive": frozenset({"spec_data"}),
    "plot_roc": frozenset({"spec_data"}),
    "process_video": frozenset({"spec_video"}),
    "query_data": frozenset({"spec_data"}),
    "read_document": frozenset({"core", "fs", "spec_read"}),
    "read_file": frozenset({"core", "fs", "git", "repl", "spec_read"}),
    "read_many_files": frozenset({"code", "fs"}),
    "read_pdf": frozenset({"core", "fs", "spec_read"}),
    "remember": frozenset({"core"}),
    "rename_file": frozenset({"code", "fs", "spec_write"}),
    "reset_kernel": frozenset({"repl"}),
    "ripgrep": frozenset({"spec_path"}),
    "run_full_analysis": frozenset({"spec_data"}),
    "run_meta_analysis": frozenset({"spec_data"}),
    "run_pca": frozenset({"spec_data"}),
    "run_python": frozenset({"core", "repl", "spec_data"}),
    "run_regression": frozenset({"spec_data"}),
    "run_stats_test": frozenset({"spec_data"}),
    "run_survival": frozenset({"spec_data"}),
    "run_time_series": frozenset({"spec_data"}),
    "search_arxiv": frozenset({"spec_research"}),
    "search_pubmed": frozenset({"spec_research"}),
    "search_youtube": frozenset({"spec_video"}),
    "search_chinese_web": frozenset({"spec_chinese_web"}),
    "semantic_file_search": frozenset({"fs", "spec_research"}),
    "session_check": frozenset({"browser"}),
    "session_login": frozenset({"browser"}),
    "spotlight_search": frozenset({"fs"}),
    "take_screenshot": frozenset({"ide_extra"}),
    "text_to_file": frozenset({"fs", "spec_write"}),
    "transcribe_audio": frozenset({"form", "spec_audio", "spec_video"}),
    "undo_last_edit": frozenset({"code", "fs", "ide_extra", "spec_write"}),
    "update_form": frozenset({"form", "fs", "spec_form"}),
    "vault_delete": frozenset({"browser"}),
    "vault_get": frozenset({"browser"}),
    "vault_list": frozenset({"browser"}),
    "vault_save": frozenset({"browser"}),
    "vision_click": frozenset({"browser"}),
    "vision_close": frozenset({"browser"}),
    "vision_detect_form_fields": frozenset({"form"}),
    "vision_extract": frozenset({"browser"}),
    "vision_fill_form_fields": frozenset({"form"}),
    "vision_open": frozenset({"browser"}),
    "vision_snap": frozenset({"browser"}),
    "vision_type": frozenset({"browser"}),
    "write_file": frozenset({"core", "fs", "git", "ide_extra", "repl", "spec_write"}),
    "ask_opencode": frozenset({"code"}),
    "ask_dev_agent": frozenset({"code"}),
    "ask_research_agent": frozenset({"code"}),
    "ask_web_search": frozenset({"code"}),
}


def tools_with_tags(*tags: str) -> set[str]:
    """Every tool name carrying at least one of the given tags."""
    tagset = set(tags)
    return {name for name, t in TOOL_TAGS.items() if t & tagset}



# EXECUTORS lives in tools/_registry/executors.py; imported after TOOL_TAGS/PLAN_* are
# defined above since executors.py also glob-imports from _registry.imports.
from tools._registry.executors import EXECUTORS

def _run_inbox_monitor_executor() -> str:
    """Run one full inbox monitor tick synchronously and return a summary."""
    import sys as _sys
    from pathlib import Path as _Path

    _jobs_dir = str(_Path(__file__).parent.parent / "jobs")
    if _jobs_dir not in _sys.path:
        _sys.path.insert(0, str(_Path(__file__).parent.parent))
    from jobs.inbox_monitor_job import load_seen_ids, save_seen_ids, run_tick

    seen_ids = load_seen_ids()
    before = len(seen_ids)
    seen_ids = run_tick(seen_ids)
    save_seen_ids(seen_ids)
    new_count = len(seen_ids) - before
    return (
        f"Inbox monitor tick complete.\n"
        f"New PDFs processed: {new_count}\n"
        f"Total seen IDs: {len(seen_ids)}\n"
        f"PDFs downloaded, converted, summarized, and sent to Telegram. "
        f"Excel tracker updated at ~/Downloads/agent/agent_tracker.xlsx."
    )


def _convert_pdf_executor(args: dict) -> str:
    import json as _json
    from pathlib import Path as _Path

    pdf_path = args["pdf_path"]
    output_dir = args.get("output_dir") or None
    md_path, md_content = pdf_to_markdown(pdf_path, output_dir)
    images = extract_pdf_images(pdf_path, output_dir or str(_Path(pdf_path).parent))
    return (
        f"Converted: {md_path}\n"
        f"Images extracted: {len(images)} ({', '.join(_Path(i).name for i in images[:5])})\n\n"
        f"{md_content[:3000]}"
    )


# Always-on guardrails (independent of Plan mode) for the handful of genuinely
# destructive operations — path-pattern denylist for filesystem-tagged tools,
# substring denylist for shell/code-exec tools. PLAN_BLOCKED_TOOLS above only
# fires in Plan mode and blocks by tool name; this fires in every mode and
# blocks by argument content, since e.g. bash_exec itself is legitimate but
# `bash_exec(command="rm -rf /")` never should be.
_DENIED_PATH_PATTERNS = (
    "*/.ssh/*", "*/.ssh", "*/.aws/*", "*/.aws", "*/.env", "*/.env.*", ".env", ".env.*",
    "/etc/*", "/etc", "/System/*", "/private/etc/*",
    "*/.git/*", "*/.git",
)
_PATH_ARG_KEYS = {"path", "file_path", "directory", "src", "dest", "output_path", "path_filter"}

_DENIED_COMMAND_SUBSTRINGS = (
    "rm -rf /", "rm -rf ~", ":(){:|:&};:", "mkfs", "dd if=",
    "> /dev/sda", "chmod -R 777 /", "curl | sh", "curl | bash", "wget | sh",
)
# Medium-risk: legitimate but shouldn't fire unsupervised. Blocks on a human
# confirm/deny via tools/confirmation.py + /chat/local-agent/confirm instead
# of an outright reject — see confirmation.py docstring.
_CONFIRM_REQUIRED_SUBSTRINGS = (
    "git commit", "git push", "git merge", "git rebase",
    "git reset --hard", "git checkout -b", "git branch -d", "git branch -D",
)
# Separate regex, not a plain substring: "rm " as a bare substring false-positives
# on "confirm ", "warm ", "term ". Word-boundary-anchor the `rm` binary itself,
# with an optional leading path (e.g. `/bin/rm`) or chained-command prefix (`&& rm`).
_RM_COMMAND_RE = _re.compile(r"(^|[;&|]\s*)(\S*/)?rm\b")
_COMMAND_ARG_KEYS = {"command", "code"}
# Same medium-risk gate as _CONFIRM_REQUIRED_SUBSTRINGS, but these tools take
# structured args (title/body), not a "command" string, so they're keyed by
# tool name instead.
_CONFIRM_REQUIRED_TOOL_NAMES = frozenset({"github_create_issue", "github_create_pr"})


def _denylist_violation(name: str, arguments: dict) -> str | None:
    """Return a rejection reason if this call trips a guardrail, else None."""
    if "fs" in TOOL_TAGS.get(name, frozenset()):
        for key in _PATH_ARG_KEYS:
            val = arguments.get(key)
            candidates = val if isinstance(val, list) else [val] if val else []
            for v in candidates:
                v = os.path.expanduser(str(v))
                if any(fnmatch.fnmatch(v, pat) for pat in _DENIED_PATH_PATTERNS):
                    return f"path '{v}' matches a denied pattern (sensitive/system location)"
    for key in _COMMAND_ARG_KEYS:
        val = arguments.get(key)
        if isinstance(val, str) and any(bad in val for bad in _DENIED_COMMAND_SUBSTRINGS):
            return f"command contains a denied pattern"
        # bash_exec/repl aren't "fs"-tagged so the loop above never inspects
        # their command string — `cat .env` sailed past every guard. Tokenize
        # and check each word against the same path denylist.
        if isinstance(val, str):
            try:
                tokens = shlex.split(val)
            except ValueError:
                tokens = val.split()
            for tok in tokens:
                tok_expanded = os.path.expanduser(tok)
                if any(fnmatch.fnmatch(tok_expanded, pat) for pat in _DENIED_PATH_PATTERNS):
                    return f"command references denied path '{tok}' (sensitive/system location)"
    return None


def _confirm_required_command(name: str, arguments: dict) -> str | None:
    """Return the offending command string if this call needs human confirmation, else None."""
    if name in _CONFIRM_REQUIRED_TOOL_NAMES:
        return f"{name}({arguments.get('title', '')!r})"
    for key in _COMMAND_ARG_KEYS:
        val = arguments.get(key)
        if isinstance(val, str) and (
            any(bad in val for bad in _CONFIRM_REQUIRED_SUBSTRINGS) or _RM_COMMAND_RE.search(val)
        ):
            return val
    return None


# Tool-result error detection. Replaces the old `"error" in result.lower() or
# "failed" in result.lower()` substring heuristic in both chat clients, which
# false-positived on legitimate content (an email *about* a failed delivery) and
# drove spurious model escalation at 3 consecutive hits. Detection is anchored to
# the START of the result so mid-text prose never matches, and neither does the
# `[subagent ... errors=N]` footer appended to subagent replies.
_KNOWN_ERROR_PREFIXES = (
    "[error",              # generic executor/client failures
    "[tool error",         # execute_tool exception wrapper
    "[blocked",            # denylist / plan-mode / confirmation gates
    "[invalid arguments",  # jsonschema validation
    "error:",              # unprefixed "Error: ..." returns in a few executors
    "error ",
    "unknown tool:",
)
# `[<name> error]`-style tags: [gmail error], [browser error], [tasks error], ...
_TAGGED_ERROR_RE = _re.compile(r"^\[[a-z_ ]*\b(error|failed|timeout|denied)\b", _re.IGNORECASE)


def is_error_result(result: str) -> bool:
    """True if a tool result string signals failure (by prefix convention)."""
    head = (result or "").lstrip()
    low = head.lower()
    if low.startswith(_KNOWN_ERROR_PREFIXES):
        return True
    return bool(_TAGGED_ERROR_RE.match(head))


# Tool results that must reach the user verbatim, bypassing the orchestrator's own
# synthesis round — that round otherwise paraphrases away machine-readable
# instructions ("reply yes", "token=...") into vague reassurance text. Both cloud
# and local tool loops check this on every tool result and short-circuit if it
# matches (see clients/cloud_client.py, clients/ollama_client.py).
def is_checkpoint_result(tool_name: str, result: str) -> bool:
    head = (result or "").lstrip()
    if head.startswith("[awaiting confirmation]"):
        return True
    if tool_name == "ask_deep_research" and "Reply **'yes'**" in head:
        return True
    return False


def execute_tool(name: str, arguments: dict) -> str:
    global PLAN_MODE_ACTIVE
    if PLAN_MODE_ACTIVE and name in PLAN_BLOCKED_TOOLS:
        return f"[blocked] {name} is unavailable in Plan mode — this is a read-only analysis tool. Do NOT call this tool."
    if name not in EXECUTORS:
        return f"Unknown tool: {name}"
    violation = _denylist_violation(name, arguments or {})
    if violation:
        result = f"[blocked] {name} rejected: {violation}"
        tool_failure_log.record_failure(name, result)
        return _with_failure_hint(name, result)
    schema = _PARAM_SCHEMAS.get(name)
    if schema is not None:
        try:
            jsonschema.validate(arguments or {}, schema)
        except jsonschema.ValidationError as e:
            result = f"[invalid arguments] {name} rejected: {e.message}"
            tool_failure_log.record_failure(name, result)
            return _with_failure_hint(name, result)
    confirm_cmd = _confirm_required_command(name, arguments or {})
    if confirm_cmd is not None:
        from tools.confirmation import request_confirmation
        token = request_confirmation(name, arguments or {}, confirm_cmd)
        return (
            f"[awaiting confirmation] {name} needs human approval before running: {confirm_cmd!r}. "
            f"Token: {token}. This call has NOT executed yet — do not assume it ran or describe "
            f"results. Tell the user to approve via POST /chat/local-agent/confirm "
            f'{{"token": "{token}", "approved": true}} (or false to deny).'
        )
    return _execute_raw(name, arguments)


# Tools with a known "empty but successful-looking" failure mode — a connector
# silently falling back to a degraded read (empty cache, 0-row parse) can return
# a labeled-success string that is_error_result() has no way to flag, since it
# carries no error prefix. Same bug class as the BrowserOS wrapper-string fix
# (CLAUDE.md "DoorDash / Uber Connectors"). Kept as a short explicit allowlist,
# not a blanket length heuristic, per the project's regex/heuristic-avoidance rule.
_EMPTY_RESULT_MIN_LEN = {
    "get_my_doordash_cart": 20,
    "get_my_doordash_orders": 20,
    "get_doordash_order_status": 20,
    "get_my_uber_trips": 20,
}


def _warn_if_suspiciously_empty(name: str, result: str) -> None:
    min_len = _EMPTY_RESULT_MIN_LEN.get(name)
    if min_len is None or is_error_result(result):
        return
    if len(result.strip()) < min_len:
        log.warning(
            "[silent-fallback-suspect] %s returned a labeled-success result "
            "under %d chars (%d) — likely an empty/degraded read, not real data: %r",
            name, min_len, len(result.strip()), result,
        )


def _execute_raw(name: str, arguments: dict) -> str:
    try:
        raw_result = EXECUTORS[name](arguments)
        from tools.tool_policy import sanitize_output
        result = sanitize_output(name, raw_result)
        _warn_if_suspiciously_empty(name, result)
        return result
    except Exception as e:
        result = f"[tool error] {name} failed: {e}"
        tool_failure_log.record_failure(name, result)
        return _with_failure_hint(name, result)


def execute_confirmed(token: str, approved: bool) -> str:
    """Run (or discard) a bash_exec/github_* call that was gated by execute_tool()'s
    confirm-required check. Called from POST /chat/local-agent/confirm once a human
    has approved/denied — the original tool-loop thread already returned the
    "[awaiting confirmation]" placeholder and moved on, so this does the real work."""
    from tools.confirmation import pop_pending
    entry = pop_pending(token)
    if entry is None:
        return f"[error] unknown or already-resolved confirmation token {token!r}"
    if not approved:
        result = f"[blocked] {entry['tool_name']} denied by user: {entry['command']!r}"
        tool_failure_log.record_failure(entry["tool_name"], result)
        return result
    return _execute_raw(entry["tool_name"], entry["arguments"])


def _with_failure_hint(name: str, result: str) -> str:
    hint = tool_failure_log.get_hint(name)
    return f"{result}\n\n{hint}" if hint else result
