"""
Focused toolset for the local-agent LangGraph agent.

Only includes tools needed for filesystem, bash, and git operations.
This avoids overloading qwen3.5:4b which fails with 75+ tools.

task="document"     → core tools only (filesystem + doc creation) — fastest, smallest prompt
task="code"         → core + code tools (grep, file_tree, parse_code, edit_file_fuzzy, etc.)
task="full" (default) → core + form + code tools — everything
"""

from tools.registry import ALL_TOOLS, tools_with_tags

_CORE_TOOL_NAMES = tools_with_tags("core")
_FORM_TOOL_NAMES = tools_with_tags("form")
_CODE_TOOL_NAMES = tools_with_tags("code")

# "browser" tag has 55 tools (DoorDash/Uber/X/vault/vision included) — way past
# the 75-tool threshold that breaks qwen3.5:4b (see module docstring). Only the
# self-contained tools/browser.py nav primitives (no BrowserOS app dependency)
# are worth it for a form-filling/coding agent; DoorDash/Uber/etc. stay
# reachable only via the web/Telegram orchestrator's ask_browser_agent.
_BROWSER_NAV_TOOL_NAMES = {
    "browser_navigate", "browser_snapshot", "browser_get_url", "browser_click",
    "browser_type", "browser_check", "browser_select", "browser_wait",
    "browser_get_content", "browser_get_attribute", "browser_run_js",
    "browser_screenshot", "browser_close", "browser_save_session",
    "browser_load_session",
}

_LOCAL_AGENT_TOOL_NAMES = _CORE_TOOL_NAMES | _FORM_TOOL_NAMES | _CODE_TOOL_NAMES | _BROWSER_NAV_TOOL_NAMES

# append_file/undo_last_edit are tagged "code" but are just as needed for safe
# document edits (undo_last_edit pairs with edit_file, which document mode already has).
_DOCUMENT_EXTRA_TOOL_NAMES = {"append_file", "undo_last_edit"}


def _tool_names_for(task: str) -> set[str]:
    if task == "document":
        return _CORE_TOOL_NAMES | _DOCUMENT_EXTRA_TOOL_NAMES
    if task == "code":
        return _CORE_TOOL_NAMES | _CODE_TOOL_NAMES
    return _LOCAL_AGENT_TOOL_NAMES


def get_local_agent_tools(task: str = "full"):
    """Return tool schemas filtered for local-agent use.

    task="document" trims to filesystem + doc-creation tools only (faster, smaller prompt).
    task="code" adds grep, file_tree, read_many_files, rename_file, copy_file, parse_code, etc.
    task="full" (default) keeps the whole set.
    """
    names = _tool_names_for(task)
    return [t for t in ALL_TOOLS if t["function"]["name"] in names]


def get_local_agent_executors(task: str = "full"):
    """Return executor map filtered for local-agent use. See get_local_agent_tools()."""
    from tools.registry import EXECUTORS
    names = _tool_names_for(task)
    return {name: fn for name, fn in EXECUTORS.items() if name in names}
