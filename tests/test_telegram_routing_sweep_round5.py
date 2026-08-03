"""
Regression and edge-case tests (Round 5) covering the primary Orchestrator Agent
behavior and its routing/bypass conditions when invoked via Telegram/WhatsApp.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

import harness as m
from clients.cloud_client import DEFAULT_CLOUD_MODEL
from clients.router import Classification


class _Recorder:
    def __init__(self, ret="ok"):
        self.ret = ret
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.ret


def test_orchestrator_runs_by_default_on_telegram(monkeypatch):
    # Telegram sends numeric chat_id, runs orchestrated by default using CLOUD_MODEL
    chat_mock = _Recorder("Orchestrated response")
    monkeypatch.setattr(m, "local_chat", chat_mock)
    monkeypatch.setattr(m, "_guard_check", lambda q, has_history=False: None)

    res, model, mode = m.run(
        query="what is the current status of the project?",
        chat_id="123456789",
    )

    assert mode == "orchestrator"
    assert model == DEFAULT_CLOUD_MODEL
    assert chat_mock.calls
    # Verify orchestrator tools are provided
    called_tools = [t["function"]["name"] for t in chat_mock.calls[0][1]["tools"]]
    assert "ask_local_agent" in called_tools
    assert "ask_web_search" in called_tools
    assert "ask_browser_agent" in called_tools


def test_orchestrator_preserves_custom_cloud_model(monkeypatch):
    chat_mock = _Recorder("Orchestrated custom cloud response")
    monkeypatch.setattr(m, "local_chat", chat_mock)
    monkeypatch.setattr(m, "_guard_check", lambda q, has_history=False: None)

    custom_model = "openrouter/anthropic/claude-haiku-4.5"
    res, model, mode = m.run(
        query="summarize my inbox",
        chat_id="123456789",
        model=custom_model,
    )

    assert mode == "orchestrator"
    assert model == custom_model
    assert chat_mock.calls[0][1]["model"] == custom_model


def test_orchestrator_falls_back_from_local_model(monkeypatch):
    chat_mock = _Recorder("Orchestrated fallback response")
    monkeypatch.setattr(m, "local_chat", chat_mock)
    monkeypatch.setattr(m, "_guard_check", lambda q, has_history=False: None)

    # Local model requested under orchestrated=True should fall back to default cloud orchestrator
    res, model, mode = m.run(
        query="summarize my inbox",
        chat_id="123456789",
        model="gemma4:12b-mlx",
    )

    assert mode == "orchestrator"
    assert model == DEFAULT_CLOUD_MODEL
    assert chat_mock.calls[0][1]["model"] == DEFAULT_CLOUD_MODEL


def test_orchestrator_bypassed_in_plan_mode(monkeypatch):
    # Ambiguity guard bypassed or mocked to return None
    monkeypatch.setattr(m, "_guard_check", lambda q, has_history=False: None)
    
    # Mocking standard non-orchestrated path LLM call
    local_chat_mock = _Recorder("Plan mode response")
    monkeypatch.setattr(m, "local_chat", local_chat_mock)

    # Plan chat_id prefix "plan_" triggers plan mode and bypasses orchestrator
    res, model, mode = m.run(
        query="create a roadmap",
        chat_id="plan_123",
    )

    assert mode != "orchestrator"
    assert model == DEFAULT_CLOUD_MODEL


def test_orchestrator_bypassed_with_direct_tools(monkeypatch):
    from clients import router as _router
    from clients import cloud_client
    monkeypatch.setattr(m, "_guard_check", lambda q, has_history=False: None)
    monkeypatch.setattr(_router, "classify_message", lambda q, channel="web", history=None: Classification(model="gemma4:12b-mlx", intent="casual", specialist_hint=None, source="test"))
    chat_mock = _Recorder("Direct tools response")
    monkeypatch.setattr(cloud_client, "chat", chat_mock)

    res, model, mode = m.run(
        query="some arbitrary non-fs question",
        chat_id="123456789",
        tools=["git_status"],
    )

    assert mode != "orchestrator"


def test_orchestrator_ambiguity_guard_precedence(monkeypatch):
    # Guard triggers a clarifying question
    clarifying_question = "Which folder do you want me to search in?"
    monkeypatch.setattr(m, "_guard_check", lambda q, has_history=False: clarifying_question)

    chat_mock = _Recorder("Orchestrated response")
    monkeypatch.setattr(m, "local_chat", chat_mock)

    res, model, mode = m.run(
        query="search files",
        chat_id="123456789",
    )

    # Guard takes precedence, returns clarifying question immediately, bypassing orchestrator chat call
    assert res == clarifying_question
    assert model == "guard"
    assert mode == "unclear"
    assert len(chat_mock.calls) == 0


def test_orchestrator_context_var_propagation(monkeypatch):
    monkeypatch.setattr(m, "_guard_check", lambda q, has_history=False: None)
    
    chat_id_captured = None
    
    def fake_local_chat(**kwargs):
        nonlocal chat_id_captured
        from tools.registry import CURRENT_CHAT_ID
        chat_id_captured = CURRENT_CHAT_ID.get()
        return "ok"

    monkeypatch.setattr(m, "local_chat", fake_local_chat)

    target_chat_id = "telegram_user_987"
    m.run(
        query="do something",
        chat_id=target_chat_id,
    )

    assert chat_id_captured == target_chat_id
