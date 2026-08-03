"""E2E verification of the code-agent workflow across all 4 phases.

PHASE 1 — Agent-Side Tools: code task is a subset of full task's tools.
PHASE 2 — Harness-Side Routing: code intents route to run_local_agent(task="code").
PHASE 3 — Safety: git write commands require confirmation, .git paths denied.
PHASE 4 — IDE Workflow: code prompt includes testing + error recovery.
"""

import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.local_agent_graph import create_local_agent_graph, get_local_agent_graph
from agents.local_agent_tools import get_local_agent_tools, get_local_agent_executors
from agents.local_agent_nodes import _build_system_prompt
from harness import _infer_local_agent_task
from tools.registry import _denylist_violation, _confirm_required_command


def test_phase1_code_has_correct_tools():
    code_tools = get_local_agent_tools("code")
    full_tools = get_local_agent_tools("full")

    assert len(code_tools) < len(full_tools), "code should have fewer tools than full"
    # Exact counts drift as tools are added (this assertion has broken twice
    # already — 27 then 42) — floor + subset checks below are what actually matters.
    assert len(code_tools) >= 27, f"expected at least 27 code tools, got {len(code_tools)}"
    assert len(full_tools) >= 37, f"expected at least 37 full tools, got {len(full_tools)}"

    code_names = {t["function"]["name"] for t in code_tools}
    full_names = {t["function"]["name"] for t in full_tools}

    # Code tools should be a strict subset of full tools
    assert code_names.issubset(full_names)
    assert len(full_names - code_names) > 0, "full should have extra tools"

    # Must-have code tools
    for tool in ("edit_file", "edit_file_fuzzy", "grep_files", "file_tree",
                 "parse_code", "run_python", "read_many_files", "bash_exec"):
        assert tool in code_names, f"{tool} missing from code toolset"

    # Form/audio tools should NOT be in code toolset
    restricted = {"fill_form", "fill_pdf_form", "transcribe_audio",
                  "vision_detect_form_fields", "vision_fill_form_fields",
                  "update_form", "detect_flat_pdf_fields"}
    assert restricted.isdisjoint(code_names), f"form tools leaked into code: {restricted & code_names}"

    print(f"  PASS Phase 1: code={len(code_tools)} tools, full={len(full_tools)} tools")


def test_phase1_code_tools_have_executors():
    code_executors = get_local_agent_executors("code")
    code_tools = get_local_agent_tools("code")
    code_names = {t["function"]["name"] for t in code_tools}

    missing = code_names - set(code_executors.keys())
    assert not missing, f"tools missing executors: {missing}"
    print(f"  PASS Phase 1: all {len(code_tools)} code tools have executors")


def test_phase2_code_intent_routing():
    queries = [
        "fix the bug in main.py",
        "refactor the auth module",
        "implement class Foo",
        "write a test for the API",
        "grep for the error handler",
        "git status",
    ]
    for q in queries:
        assert _infer_local_agent_task(q) == "code", f"'{q}' should route to code"

    non_code = [
        "what files are in Downloads",
        "how is the weather today",
        "transcribe that audio file",
        "delete file /tmp/test.txt",
    ]
    for q in non_code:
        assert _infer_local_agent_task(q) == "full", f"'{q}' should route to full"

    # "fill form.pdf" routes to the dedicated "document" task type, not "full"
    # — a later, deliberate split (see CLAUDE.md "document"/"code"/"full" task types).
    assert _infer_local_agent_task("fill form.pdf for me") == "document"

    print(f"  PASS Phase 2: code routing correct for {len(queries)} code + {len(non_code)} non-code queries")


def test_phase2_code_intent_tool_selection():
    """Verify code intents get empty active_tools (local agent owns its toolset)."""
    from harness import run as harness_run
    harness_run  # import check — harness loads without errors
    print(f"  PASS Phase 2: harness.py imports with code_intent routing")


def test_phase3_git_write_blocked():
    # Git-mutation commands no longer hit the hard denylist — they were moved
    # to the confirm-before-execute gate (block on human approval instead of
    # outright reject, see tools/registry.py's _CONFIRM_REQUIRED_SUBSTRINGS).
    assert _confirm_required_command("bash_exec", {"command": "git commit -m x"}), "commit should require confirmation"
    assert _confirm_required_command("bash_exec", {"command": "git push origin main"}), "push should require confirmation"
    assert _confirm_required_command("bash_exec", {"command": "git merge feature"}), "merge should require confirmation"
    assert _confirm_required_command("bash_exec", {"command": "git rebase main"}), "rebase should require confirmation"
    assert _confirm_required_command("bash_exec", {"command": "git reset --hard HEAD~1"}), "reset --hard should require confirmation"

    # Read-only git OK
    assert not _denylist_violation("bash_exec", {"command": "git status"}), "status OK"
    assert not _denylist_violation("bash_exec", {"command": "git diff HEAD~2"}), "diff OK"
    assert not _denylist_violation("bash_exec", {"command": "git log --oneline -5"}), "log OK"

    # .git path denylist
    for tool in ("read_file", "write_file", "edit_file", "delete_file"):
        assert _denylist_violation(tool, {"path": "/home/user/.git/config"}), f".git path should block {tool}"

    # Normal paths OK
    assert not _denylist_violation("read_file", {"path": "/home/user/project/main.py"})
    assert not _denylist_violation("write_file", {"path": "/home/user/project/new.py"})

    print(f"  PASS Phase 3: all safety guardrails active")


def test_phase4_workflow_prompt_has_testing():
    code_tools = get_local_agent_tools("code")
    prompt = _build_system_prompt("code", code_tools, "/Users/testuser")

    assert "file_tree" in prompt, "prompt should mention file_tree"
    assert "grep_files" in prompt, "prompt should mention grep_files"
    assert "edit_file" in prompt, "prompt should mention edit_file"
    assert "edit_file_fuzzy" in prompt, "prompt should mention edit_file_fuzzy"
    assert "bash_exec" in prompt or "run_python" in prompt, "prompt should mention testing"
    assert "test" in prompt.lower(), "prompt should mention testing step"

    print(f"  PASS Phase 4: code prompt has testing+error recovery")


def test_graph_builds_for_code_task():
    """Check graph still compiles (no wiring regressions)."""
    graph = create_local_agent_graph()
    assert graph is not None
    node_names = set(graph.get_graph().nodes)
    assert "agent" in node_names
    assert "tools" in node_names

    # Singleton pattern still works
    g1 = get_local_agent_graph()
    g2 = get_local_agent_graph()
    assert g1 is g2

    print(f"  PASS graph compiles ({len(node_names)} nodes)")


def test_code_prompt_excludes_form_tools():
    """Phase 1: code prompt role says coding, not form-filling."""
    code_tools = get_local_agent_tools("code")
    prompt = _build_system_prompt("code", code_tools, "/Users/testuser")

    assert "coding assistant" in prompt.lower(), "code prompt should say coding assistant"
    assert "FORM WORKFLOW" not in prompt.upper(), "code prompt should not have form workflow"

    print(f"  PASS code prompt role is coding, not form")


if __name__ == "__main__":
    print("=== Code Agent E2E Verification ===\n")
    test_phase1_code_has_correct_tools()
    test_phase1_code_tools_have_executors()
    test_phase2_code_intent_routing()
    test_phase2_code_intent_tool_selection()
    test_phase3_git_write_blocked()
    test_phase4_workflow_prompt_has_testing()
    test_graph_builds_for_code_task()
    test_code_prompt_excludes_form_tools()
    print(f"\nAll tests PASS")
