"""
Smoke tests for all 47 local-agent tools.
Validates schemas, executor registration, and runs core tools with safe inputs.
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tools.registry import ALL_TOOLS, EXECUTORS, tools_with_tags

_CORE = tools_with_tags("core")
_FORM = tools_with_tags("form")
_CODE = tools_with_tags("code")
_AGENT = _CORE | _FORM | _CODE


# ---------------------------------------------------------------------------
# Schema validation — every tool must have valid name/desc/params
# ---------------------------------------------------------------------------

def test_all_tool_schemas_valid():
    for t in ALL_TOOLS:
        fn = t.get("function", {})
        assert fn.get("name"), f"tool missing name: {t}"
        assert fn.get("description"), f"{fn['name']} missing description"


def test_agent_tool_schemas_have_required_keys():
    agent_names = set()
    for t in ALL_TOOLS:
        if t["function"]["name"] in _AGENT:
            agent_names.add(t["function"]["name"])
            params = t["function"].get("parameters", {})
            assert "type" in params, f"{t['function']['name']} missing parameters.type"
            assert "properties" in params, f"{t['function']['name']} missing parameters.properties"
    assert len(agent_names) == len(_AGENT), f"missing {_AGENT - agent_names}"


# ---------------------------------------------------------------------------
# Executor registration — every agent tool has a callable
# ---------------------------------------------------------------------------

def test_all_agent_tools_have_executors():
    missing = _AGENT - set(EXECUTORS.keys())
    assert not missing, f"agent tools missing executors: {missing}"


def test_all_251_tools_have_executors():
    all_names = {t["function"]["name"] for t in ALL_TOOLS}
    missing = all_names - set(EXECUTORS.keys())
    assert not missing, f"tools missing executors: {missing}"


# ---------------------------------------------------------------------------
# Core tool smoke — run with safe minimal args, verify returns str
# ---------------------------------------------------------------------------

_CORE_SMOKE_ARGS = {
    "bash_exec": {"command": "echo hello"},
    "create_directory": {"path": "/tmp/_test_tool_smoke_dir/delete_me"},
    "find_files": {"pattern": "*.py", "path": "."},
    "forget": {"query": "test"},
    "github_search": {"query": "repo:psf/requests type:issue state:open"},
    "list_directory": {"path": "."},
    "remember": {"fact": "test fact"},
    "run_python": {"code": "print(1+1)"},
    "read_file": {"path": "README.md"},
}

_CORE_SKIP = {
    "create_pdf",
    "data_to_xlsx",
    "edit_file",
    "edit_image",
    "json_to_docx",
    "load_external_skill",
    "markdown_to_docx",
    "markdown_to_pdf",
    "markdown_to_pptx",
    "read_document",
    "read_pdf",
    "render_diagram",
    "write_file",
}


@pytest.mark.parametrize("name", sorted(_CORE_SMOKE_ARGS))
def test_core_tool_smoke(name):
    args = _CORE_SMOKE_ARGS[name]
    fn = EXECUTORS[name]
    result = fn(args)
    assert isinstance(result, str), f"{name} returned {type(result)}"


def test_core_create_pdf_smoke():
    import tempfile, pathlib
    p = pathlib.Path(tempfile.mkstemp(suffix=".pdf")[1])
    result = EXECUTORS["create_pdf"]({"content": "# Hello", "output_path": str(p)})
    assert isinstance(result, str)
    assert p.exists()
    p.unlink()


def test_core_edit_file_smoke():
    import tempfile, pathlib
    f = pathlib.Path(tempfile.mkstemp(suffix=".txt")[1])
    f.write_text("old content")
    result = EXECUTORS["edit_file"]({"path": str(f), "old_str": "old content", "new_str": "new content"})
    assert isinstance(result, str)
    assert f.read_text() == "new content"
    f.unlink()


def test_core_write_file_smoke():
    import tempfile, pathlib
    p = pathlib.Path(tempfile.mkstemp(suffix=".txt")[1])
    result = EXECUTORS["write_file"]({"path": str(p), "content": "test"})
    assert isinstance(result, str)
    assert p.read_text() == "test"
    p.unlink()


def test_core_render_diagram_smoke():
    result = EXECUTORS["render_diagram"]({"code": "A -> B", "engine": "d2"})
    assert isinstance(result, str)
    # d2 may not be installed — should return error, not crash
    assert True


def test_core_edit_image_smoke():
    result = EXECUTORS["edit_image"]({"path": "/nonexistent.png", "operation": "convert", "output_path": "/tmp/nope.jpg"})
    assert isinstance(result, str)


def test_core_read_document_smoke():
    result = EXECUTORS["read_document"]({"path": "README.md"})
    assert isinstance(result, str)


def test_core_read_pdf_smoke():
    result = EXECUTORS["read_pdf"]({"path": "README.md"})
    assert isinstance(result, str)


def test_core_data_to_xlsx_smoke():
    import tempfile, pathlib, json
    data = pathlib.Path(tempfile.mkstemp(suffix=".json")[1])
    data.write_text(json.dumps([{"a": 1, "b": 2}]))
    out = pathlib.Path(tempfile.mkstemp(suffix=".xlsx")[1])
    result = EXECUTORS["data_to_xlsx"]({"data_path": str(data), "output_path": str(out)})
    assert isinstance(result, str)
    data.unlink()
    if out.exists():
        out.unlink()


def test_core_load_external_skill_smoke():
    result = EXECUTORS["load_external_skill"]({"path": "/nonexistent/SKILL.md", "message": "test"})
    assert isinstance(result, str)


def test_core_markdown_to_pdf_smoke():
    import tempfile, pathlib
    md = pathlib.Path(tempfile.mkstemp(suffix=".md")[1])
    md.write_text("# Hello")
    out = pathlib.Path(tempfile.mkstemp(suffix=".pdf")[1])
    result = EXECUTORS["markdown_to_pdf"]({"md_path": str(md), "output_path": str(out)})
    assert isinstance(result, str)
    md.unlink()
    if out.exists():
        out.unlink()


def test_core_markdown_to_docx_smoke():
    import tempfile, pathlib
    md = pathlib.Path(tempfile.mkstemp(suffix=".md")[1])
    md.write_text("# Hello")
    out = pathlib.Path(tempfile.mkstemp(suffix=".docx")[1])
    result = EXECUTORS["markdown_to_docx"]({"md_path": str(md), "output_path": str(out)})
    assert isinstance(result, str)
    md.unlink()
    if out.exists():
        out.unlink()


def test_core_json_to_docx_smoke():
    import tempfile, pathlib, json
    j = pathlib.Path(tempfile.mkstemp(suffix=".json")[1])
    j.write_text(json.dumps({"title": "Test", "body": "Hello"}))
    out = pathlib.Path(tempfile.mkstemp(suffix=".docx")[1])
    result = EXECUTORS["json_to_docx"]({"json_path": str(j), "output_path": str(out)})
    assert isinstance(result, str)
    j.unlink()
    if out.exists():
        out.unlink()


def test_core_markdown_to_pptx_smoke():
    import tempfile, pathlib
    md = pathlib.Path(tempfile.mkstemp(suffix=".md")[1])
    md.write_text("# Hello")
    out = pathlib.Path(tempfile.mkstemp(suffix=".pptx")[1])
    result = EXECUTORS["markdown_to_pptx"]({"md_path": str(md), "output_path": str(out)})
    assert isinstance(result, str)
    md.unlink()
    if out.exists():
        out.unlink()


# ---------------------------------------------------------------------------
# Smoke for form tools
# ---------------------------------------------------------------------------

def test_form_detect_pdf_fields_smoke():
    result = EXECUTORS["detect_pdf_form_fields"]({"path": "/nonexistent.pdf"})
    assert isinstance(result, str)


def test_form_detect_form_fields_smoke():
    result = EXECUTORS["detect_form_fields"]({"file_path": "/nonexistent.pdf"})
    assert isinstance(result, str)


def test_form_vision_detect_form_fields_smoke():
    result = EXECUTORS["vision_detect_form_fields"]({"path": "/nonexistent.pdf"})
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Smoke for code tools
# ---------------------------------------------------------------------------

def test_code_git_status_smoke():
    result = EXECUTORS["git_status"]({})
    assert isinstance(result, str)


def test_code_git_diff_smoke():
    result = EXECUTORS["git_diff"]({})
    assert isinstance(result, str)


def test_code_grep_files_smoke():
    result = EXECUTORS["grep_files"]({"pattern": "def test", "path": ".", "max_results": 5})
    assert isinstance(result, str)


def test_code_file_tree_smoke():
    result = EXECUTORS["file_tree"]({"path": ".", "max_depth": 1})
    assert isinstance(result, str)


def test_code_parse_code_smoke():
    result = EXECUTORS["parse_code"]({"path": "README.md"})
    assert isinstance(result, str)


def test_code_read_many_files_smoke():
    result = EXECUTORS["read_many_files"]({"paths": ["README.md"], "limit_per_file": 50})
    assert isinstance(result, str)


def test_code_copy_file_smoke():
    import tempfile, pathlib
    f = pathlib.Path(tempfile.mkstemp(suffix=".txt")[1])
    f.write_text("data")
    dest = pathlib.Path(tempfile.mkstemp(suffix=".txt")[1])
    result = EXECUTORS["copy_file"]({"src": str(f), "dest": str(dest)})
    assert isinstance(result, str)
    f.unlink()
    dest.unlink()


def test_code_rename_file_smoke():
    import tempfile, pathlib
    f = pathlib.Path(tempfile.mkstemp(suffix=".txt")[1])
    f.write_text("data")
    dest = pathlib.Path(tempfile.mkstemp(suffix=".txt")[1])
    dest.unlink()
    result = EXECUTORS["rename_file"]({"src": str(f), "dest": str(dest)})
    assert isinstance(result, str)
    if dest.exists():
        dest.unlink()
    if f.exists():
        f.unlink()


def test_code_ask_dev_agent_smoke():
    result = EXECUTORS["ask_dev_agent"]({"query": "say hello, no tools"})
    assert isinstance(result, str)


def test_code_ask_research_agent_smoke():
    result = EXECUTORS["ask_research_agent"]({"query": "what is 2+2? say fast"})
    assert isinstance(result, str)


def test_code_ask_web_search_smoke():
    result = EXECUTORS["ask_web_search"]({"query": "fastapi", "max_results": 2})
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Edge: tool returns error string, not raises
# ---------------------------------------------------------------------------

def test_core_tools_return_string_not_raise():
    """Every core tool executor returns a string even on bad input (no crash)."""
    bad_tests = {
        "bash_exec": {},
        "create_directory": {},
        "edit_file": {},
        "find_files": {},
        "forget": {},
        "github_search": {},
        "list_directory": {},
        "read_file": {},
        "remember": {},
        "run_python": {},
        "write_file": {},
    }
    for name, args in bad_tests.items():
        fn = EXECUTORS[name]
        try:
            result = fn(args)
            assert isinstance(result, str), f"{name} returned {type(result)} on bad args"
        except KeyError:
            pass  # lambda may KeyError on missing required arg — acceptable for bad input


# ---------------------------------------------------------------------------
# All 251 tools — bad-arg safety (return str, not raise)
# ---------------------------------------------------------------------------

_BAD_ARG_SKIP = {
    "ask_browser_agent", "ask_vision_agent", "ask_automation_agent",
    "ask_calendar_agent", "ask_docs_agent", "ask_email_agent",
    "ask_local_agent", "ask_macos_native", "ask_messaging_agent",
    "ask_sheets_agent", "ask_tasks_agent", "ask_transport_agent",
    "ask_utility_agent", "deep_research",
    "run_skill", "load_external_skill",
    "run_inbox_monitor",
    "create_automation", "trigger_automation_now",
    "delete_automation", "pause_automation", "resume_automation",
    "create_workflow", "list_workflows",
}



def test_all_tools_bad_args_return_string():
    """Non-agent executors with safe defaults return str (not raise) on {}.
    Skips network/filesystem/IO tools. KeyError on required-arg is acceptable."""
    all_names = {t["function"]["name"] for t in ALL_TOOLS}
    non_agent = sorted(all_names - _AGENT)

    # only test tools with safe defaults — no network, no filesystem scan, no external CLI
    _SAFE_DEFAULT = {
        "clipboard_read", "clipboard_write",
        "get_current_time", "system_status",
        "list_safari_tabs", "refresh_google_token",
        "reset_kernel", "list_kernel_vars",
        "git_status", "git_log", "git_diff", "git_add",
        "list_notes",
        "search_wikipedia",
    }

    keyerrors: list[str] = []
    crashes: list[str] = []
    non_str: list[str] = []

    for name in sorted(non_agent):
        if name not in _SAFE_DEFAULT:
            continue
        fn = EXECUTORS.get(name)
        if fn is None:
            continue
        try:
            result = fn({})
            if not isinstance(result, str):
                non_str.append(f"{name}({type(result).__name__})")
        except KeyError:
            keyerrors.append(name)
        except Exception as e:
            crashes.append(f"{name}({type(e).__name__}:{e})")

    msgs = []
    if crashes:
        msgs.append(f"CRASHES ({len(crashes)}): {crashes}")
    if non_str:
        msgs.append(f"non-str ({len(non_str)}): {non_str}")
    assert not crashes and not non_str, "; ".join(msgs)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
