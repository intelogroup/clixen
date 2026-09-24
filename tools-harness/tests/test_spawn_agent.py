"""M5: restricted toolsets + agent fan-out."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import run_loop, spawn
from store import run_store as rs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "_DB_PATH", tmp_path / "runs.sqlite")
    monkeypatch.setattr(rs, "_vault_secret_values", lambda: frozenset())


def test_unavailable_tool_is_denied_not_executed(monkeypatch):
    executed = []
    monkeypatch.setattr(run_loop, "_execute_tool",
                        lambda n, a: executed.append(n) or "ran")
    rid = rs.create_run("g")
    rs.append_event(rid, "user_msg", {"text": "go"})
    out = run_loop.run_rounds(rid, lambda m, t: {
        "text": "ok", "tool_calls": [{"id": "t1", "name": "delete_everything",
                                      "args": {}}]}, tools=["web_search"])
    assert executed == [], "a tool outside the allowlist must never execute"
    res = [e for e in rs.get_events(rid) if e["kind"] == "tool_result"][0]
    assert "not available" in res["payload"]["result"]
    assert "web_search" in res["payload"]["result"]


def test_empty_allowlist_disables_tool_execution(monkeypatch):
    executed = []
    monkeypatch.setattr(run_loop, "_execute_tool",
                        lambda n, a: executed.append(n) or "ran")
    rid = rs.create_run("g")
    run_loop.run_rounds(rid, lambda m, t: {
        "text": "", "tool_calls": [{"id": "t1", "name": "web_search",
                                    "args": {}}]}, tools=[])
    assert executed == []


def test_spawn_agent_links_child_to_parent(monkeypatch):
    parent = rs.create_run("parent goal")
    child = spawn.spawn_agent(parent, "child goal", tools=["web_search"],
                              execute=lambda rid: None)
    run_ev = [e for e in rs.get_events(child) if e["kind"] == "run"][-1]
    assert run_ev["payload"]["parent_run_id"] == parent
    assert run_ev["payload"]["tools"] == ["web_search"]


def test_child_answer_lands_in_parent_journal(monkeypatch):
    parent = rs.create_run("parent goal")
    child = rs.create_run("child goal", trigger="agent-mail")
    assert spawn.deliver_to_parent(parent, child, "the answer is 42")
    msgs = [e for e in rs.get_events(parent) if e["kind"] == "user_msg"]
    assert msgs[-1]["payload"] == {"via": "agent", "from": child,
                                   "text": "the answer is 42"}


def test_delivery_refused_when_parent_finished():
    parent = rs.create_run("parent goal")
    rs.set_status(parent, "running")
    rs.set_status(parent, "succeeded")
    assert spawn.deliver_to_parent(parent, "c1", "late") is False
    assert not [e for e in rs.get_events(parent) if e["kind"] == "user_msg"]


def test_spawn_requires_a_real_parent():
    with pytest.raises(KeyError):
        spawn.spawn_agent("nope", "goal", tools=[])


def test_parent_sees_agent_message_on_next_round(monkeypatch):
    """The parent's loop replays the delivered agent answer as a user turn."""
    seen = []
    parent = rs.create_run("parent")
    child = rs.create_run("child", trigger="agent-mail")
    spawn.deliver_to_parent(parent, child, "finding: 42")

    def model(messages, tools):
        seen.append(list(messages))
        return {"text": "done", "tool_calls": []}

    run_loop.run_rounds(parent, model, tools=[])
    assert any(m.get("content") == "finding: 42" for m in seen[0])
