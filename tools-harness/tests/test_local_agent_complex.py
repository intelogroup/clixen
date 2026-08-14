"""
Complex/E2E coverage for the local-agent (LangGraph) loop, beyond the unit-level
node tests in test_local_agent_verify.py and test_local_agent_skill_prompt.py.

Covers 4 areas:
1. Multi-step tool chaining through tool_node (find_files -> read_file -> edit_file).
2. Skill-scoped tool restriction actually blocks execution, not just narrows the
   advertised list (agents/local_agent_nodes.py:926's allowed_tool_names gate).
3. Confirm-gate fires for a destructive command proposed by the local agent, and
   DENY/APPROVE via tools/registry.execute_confirmed() resolves it correctly.
4. golden_query-shaped regression case added to skill_eval.py's suite.

All model/tool calls are mocked or use real tmp_path fixtures — no live LLM/network.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

from langchain_core.messages import AIMessage

from agents.local_agent_nodes import tool_node, execute_tool
from agents.local_agent_state import LocalAgentState
from tools.confirmation import request_confirmation
from tools.registry import execute_confirmed, _confirm_required_command


def _tool_call(name, args, call_id="c1"):
    return {"name": name, "args": args, "id": call_id}


# ── 1. Multi-step tool chaining ─────────────────────────────────────────────

def test_tool_node_chains_find_then_read(tmp_path):
    """Two tool calls in one AIMessage both execute and produce ToolMessages,
    proving tool_node handles a multi-call round without dropping any."""
    f = tmp_path / "note.txt"
    f.write_text("hello world")

    ai = AIMessage(content="", tool_calls=[
        _tool_call("find_files", {"pattern": "note.txt", "root": str(tmp_path)}, "c1"),
        _tool_call("read_file", {"path": str(f)}, "c2"),
    ])
    state = LocalAgentState(messages=[ai], task="code")

    result = asyncio.run(tool_node(state))

    assert len(result["messages"]) == 2
    ids = {m.tool_call_id for m in result["messages"]}
    assert ids == {"c1", "c2"}
    read_msg = next(m for m in result["messages"] if m.tool_call_id == "c2")
    assert "hello world" in read_msg.content


def test_tool_node_prevents_repeating_identical_read_across_rounds(tmp_path):
    f = tmp_path / "memo.txt"
    f.write_text("already read")
    first = AIMessage(content="", tool_calls=[_tool_call("read_file", {"path": str(f)}, "first")])
    second = AIMessage(content="", tool_calls=[_tool_call("read_file", {"path": str(f)}, "second")])
    state = LocalAgentState(
        messages=[first, *asyncio.run(tool_node(LocalAgentState(messages=[first], task="code")))["messages"], second],
        task="code",
    )

    result = asyncio.run(tool_node(state))

    assert "duplicate read prevented" in result["messages"][0].content
    assert "already read" not in result["messages"][0].content


# ── 2. Skill-scoped tool restriction is enforced at execution, not just advertising ─

def test_skill_tools_block_execution_of_unlisted_tool(tmp_path):
    """A skill that names only find_files/read_file must not let the model
    execute bash_exec even if it hallucinates the call — the allow-list applies
    to what actually runs, not just what's shown as available."""
    f = tmp_path / "secret.txt"
    f.write_text("data")

    ai = AIMessage(content="", tool_calls=[
        _tool_call("bash_exec", {"command": f"cat {f}"}, "c1"),
    ])
    state = LocalAgentState(
        messages=[ai], task="full",
        skill_tools=["find_files", "read_file"],
    )

    result = asyncio.run(tool_node(state))

    assert len(result["messages"]) == 1
    assert "[blocked]" in result["messages"][0].content
    assert "not allowed in this agent's scoped manifest" in result["messages"][0].content


def test_skill_tools_allow_listed_tool_through(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hi")
    ai = AIMessage(content="", tool_calls=[_tool_call("read_file", {"path": str(f)}, "c1")])
    state = LocalAgentState(messages=[ai], task="full", skill_tools=["read_file"])

    result = asyncio.run(tool_node(state))

    assert "[blocked]" not in result["messages"][0].content
    assert "hi" in result["messages"][0].content


# ── 3. Confirm-gate fires + DENY/APPROVE resolve it ─────────────────────────

def test_local_agent_git_push_requires_confirmation():
    """A destructive command routed through execute_tool (which local-agent's
    tool_node calls under the hood via _registry_execute_tool) must not run
    immediately — it should come back as an [awaiting confirmation] placeholder."""
    assert _confirm_required_command("bash_exec", {"command": "git push origin main"})

    result, is_error = execute_tool({
        "name": "bash_exec", "args": {"command": "git push origin main"}, "id": "c1",
    })

    assert "[awaiting confirmation]" in result.content
    assert is_error is False  # placeholder is not a failure, agent should wait


def test_confirm_gate_deny_blocks_execution():
    token = request_confirmation("bash_exec", {"command": "rm -rf ./build"}, "rm -rf ./build")
    with patch("tools.registry.EXECUTORS", {"bash_exec": lambda args: "SHOULD NOT RUN"}):
        result = execute_confirmed(token, approved=False)
    assert "denied" in result.lower() or "cancel" in result.lower()
    assert "SHOULD NOT RUN" not in result


def test_confirm_gate_approve_executes():
    token = request_confirmation("bash_exec", {"command": "echo ok"}, "echo ok")
    with patch("tools.registry.EXECUTORS", {"bash_exec": lambda args: "ran: " + args["command"]}):
        result = execute_confirmed(token, approved=True)
    assert "ran: echo ok" in result


def test_existing_file_overwrite_requires_confirmation(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("before")

    assert _confirm_required_command("write_file", {"path": str(target), "content": "after"})
    result, is_error = execute_tool({
        "name": "write_file", "args": {"path": str(target), "content": "after"}, "id": "c1",
    })

    assert "[awaiting confirmation]" in result.content
    assert is_error is False
    assert target.read_text() == "before"


def test_document_export_requires_confirmation_even_for_new_destination(tmp_path):
    output = tmp_path / "new.pdf"

    assert _confirm_required_command("create_pdf", {"content": "# draft", "output_path": str(output)})
    result, is_error = execute_tool({
        "name": "create_pdf", "args": {"content": "# draft", "output_path": str(output)}, "id": "c1",
    })

    assert "[awaiting confirmation]" in result.content
    assert is_error is False
    assert not output.exists()


def test_approved_overwrite_creates_private_version(tmp_path, monkeypatch):
    target = tmp_path / "versioned.txt"
    target.write_text("before")
    version_root = tmp_path / "versions"
    monkeypatch.setattr("tools.document_output.data_dir", lambda: version_root)

    token = request_confirmation(
        "write_file", {"path": str(target), "content": "after"}, "overwrite existing file"
    )
    result = execute_confirmed(token, approved=True)

    assert "after" in target.read_text()
    from tools.document_output import list_versions
    versions = list_versions(str(target))
    assert versions and Path(versions[0]["backup"]).read_text() == "before"


def test_restore_document_version_requires_approval_and_is_reversible(tmp_path, monkeypatch):
    from tools.document_output import archive_existing, list_versions

    target = tmp_path / "restore-me.txt"
    target.write_text("original")
    monkeypatch.setattr("tools.document_output.data_dir", lambda: tmp_path / "versions")
    backup = archive_existing(str(target))
    target.write_text("changed")

    assert _confirm_required_command("restore_document_version", {"path": str(target), "backup": backup})
    pending, is_error = execute_tool({
        "name": "restore_document_version",
        "args": {"path": str(target), "backup": backup},
        "id": "restore-1",
    })
    assert "[awaiting confirmation]" in pending.content
    assert is_error is False

    import re
    token = re.search(r"Token: ([a-f0-9]+)", pending.content).group(1)
    result = execute_confirmed(token, approved=True)
    assert '"ok": true' in result.lower()
    assert target.read_text() == "original"
    assert len(list_versions(str(target))) == 2
