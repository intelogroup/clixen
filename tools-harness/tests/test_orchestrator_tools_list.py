"""Drift guard: every ASK_*_AGENT_SCHEMA and the memory tools must actually reach
the orchestrator's tool-calling loop, not just exist in ALL_TOOLS/orchestrator_tools.py.

Two real bugs (2026-07-13) slipped through because a schema was defined with a working
executor but never added to harness.py's `orchestrator_tools` list — the LLM had no such
function to call. This test would have caught both.
"""
import re

import harness
import tools.orchestrator_tools as ot


def _agent_schema_names():
    names = set()
    for attr in dir(ot):
        if re.match(r"^ASK_.*_AGENT_SCHEMA$", attr):
            schema = getattr(ot, attr)
            names.add(schema["function"]["name"])
    return names


def test_every_agent_schema_reaches_orchestrator(monkeypatch):
    captured = {}

    def fake_local_chat(**kwargs):
        captured["tools"] = kwargs.get("tools") or []
        return "ok"

    monkeypatch.setattr(harness, "local_chat", fake_local_chat)
    monkeypatch.setattr(harness, "_guard_check", lambda *a, **k: None)
    monkeypatch.setattr(harness, "trim_to_budget", lambda h, m, q, chat_id=None: h)

    harness.run(query="hello, how are you today?", chat_id=None)

    offered = {t["function"]["name"] for t in captured["tools"]}
    missing = _agent_schema_names() - offered
    assert not missing, f"ASK_*_AGENT_SCHEMA names missing from orchestrator_tools: {missing}"


def test_memory_tools_reach_orchestrator(monkeypatch):
    captured = {}

    def fake_local_chat(**kwargs):
        captured["tools"] = kwargs.get("tools") or []
        return "ok"

    monkeypatch.setattr(harness, "local_chat", fake_local_chat)
    monkeypatch.setattr(harness, "_guard_check", lambda *a, **k: None)
    monkeypatch.setattr(harness, "trim_to_budget", lambda h, m, q, chat_id=None: h)

    harness.run(query="hello, how are you today?", chat_id=None)

    offered = {t["function"]["name"] for t in captured["tools"]}
    assert {"remember", "forget", "search_sessions"} <= offered


def test_update_task_plan_reaches_orchestrator(monkeypatch):
    captured = {}

    def fake_local_chat(**kwargs):
        captured["tools"] = kwargs.get("tools") or []
        return "ok"

    monkeypatch.setattr(harness, "local_chat", fake_local_chat)
    monkeypatch.setattr(harness, "_guard_check", lambda *a, **k: None)
    monkeypatch.setattr(harness, "trim_to_budget", lambda h, m, q, chat_id=None: h)

    harness.run(query="hello, how are you today?", chat_id=None)

    offered = {t["function"]["name"] for t in captured["tools"]}
    assert "update_task_plan" in offered


def test_plan_block_reaches_orchestrator_system_prompt(monkeypatch):
    """Mirrors test_recall_block_reaches_orchestrator_system_prompt: proves the
    plan_store.plan_block() injection at harness.py's orchestrator branch actually
    wires in, not just that plan_store exists."""
    from store import plan_store

    captured = {}

    def fake_local_chat(**kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt") or ""
        return "ok"

    monkeypatch.setattr(harness, "local_chat", fake_local_chat)
    monkeypatch.setattr(harness, "_guard_check", lambda *a, **k: None)
    monkeypatch.setattr(harness, "trim_to_budget", lambda h, m, q, chat_id=None: h)
    monkeypatch.setattr(plan_store, "plan_block", lambda run_id: "Current task plan:\n  [ ] SENTINEL-PLAN-99")

    harness.run(query="do three things for me", chat_id=None)

    assert "SENTINEL-PLAN-99" in captured["system_prompt"]


def test_recall_block_reaches_orchestrator_system_prompt(monkeypatch):
    """The orchestrator branch builds its own system prompt and returns before
    reaching the non-orchestrated path's memory_recall() call further down in
    run() — passive cross-session recall was silently dead for orchestrator-routed
    turns (the default route since cloud-first). Proves the fix actually wires in,
    not just that memory_recall() exists."""
    captured = {}
    sentinel = "## What you remember about the user\n- SENTINEL-SENTINEL-42\n"

    def fake_local_chat(**kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt") or ""
        return "ok"

    monkeypatch.setattr(harness, "local_chat", fake_local_chat)
    monkeypatch.setattr(harness, "_guard_check", lambda *a, **k: None)
    monkeypatch.setattr(harness, "trim_to_budget", lambda h, m, q, chat_id=None: h)
    monkeypatch.setattr(harness, "memory_recall", lambda query: sentinel)

    harness.run(query="what is my favorite color?", chat_id=None)

    assert sentinel in captured["system_prompt"]
