import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clients.ollama_client import _build_messages, _parse_text_tool_call, _run_local


def test_parse_text_tool_call_returns_known_function_and_args():
    content = 'before {"name":"web_search","arguments":{"query":"bitcoin price"}} after'

    parsed = _parse_text_tool_call(content, {"web_search"})

    assert parsed == ("web_search", {"query": "bitcoin price"})


def test_parse_text_tool_call_ignores_unknown_function():
    content = '{"name":"not_registered","arguments":{"query":"bitcoin price"}}'

    parsed = _parse_text_tool_call(content, {"web_search"})

    assert parsed is None


def test_build_messages_uses_developer_role_for_gemma_tool_calling():
    messages = _build_messages(
        "check the weather",
        [],
        model="gemma4:e2b",
        tools=[{"function": {"name": "web_search"}}],
    )

    assert messages[0]["role"] == "developer"
    assert "function calling" in messages[0]["content"]


def test_build_messages_uses_system_role_for_non_gemma_tool_calling():
    messages = _build_messages(
        "check the weather",
        [],
        model="qwen3:4b",
        tools=[{"function": {"name": "web_search"}}],
    )

    assert messages[0]["role"] == "system"


def test_run_local_sets_qwen_tool_options_and_disables_thinking():
    mock_client = Mock()
    mock_client.chat.return_value = SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))

    with patch("clients.ollama_client._get_client", return_value=mock_client):
        _run_local("qwen3:4b", [{"role": "user", "content": "hi"}], tools=[{"function": {"name": "web_search"}}])

    _, kwargs = mock_client.chat.call_args
    assert kwargs["think"] is False
    assert kwargs["options"]["temperature"] == 0.7
    assert kwargs["options"]["top_p"] == 0.8
    assert kwargs["options"]["top_k"] == 20
    assert kwargs["options"]["repetition_penalty"] == 1.05


def test_run_local_sets_gemma_tool_options():
    mock_client = Mock()
    mock_client.chat.return_value = SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))

    with patch("clients.ollama_client._get_client", return_value=mock_client):
        _run_local("gemma4:e2b", [{"role": "user", "content": "hi"}], tools=[{"function": {"name": "web_search"}}])

    _, kwargs = mock_client.chat.call_args
    # Gemma4: thinking mode is disabled for ALL calls (not just tool calls) —
    # the default think=True burns the entire num_predict budget on reasoning
    # and produces empty responses. See clients/ollama_client.py:_run_local.
    assert kwargs["think"] is False
    assert kwargs["options"]["temperature"] == 1.0
    assert kwargs["options"]["top_p"] == 0.95
    assert kwargs["options"]["top_k"] == 64


def test_run_local_disables_gemma_thinking_for_casual_chat():
    """gemma4:12b-mlx/e2b have thinking enabled by default — must be off in casual chat too."""
    mock_client = Mock()
    mock_client.chat.return_value = SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))

    with patch("clients.ollama_client._get_client", return_value=mock_client):
        _run_local("gemma4:12b-mlx", [{"role": "user", "content": "hi"}], tools=[])

    _, kwargs = mock_client.chat.call_args
    assert kwargs["think"] is False
    # Casual chat caps num_predict=512 to leave room for thorough answers
    assert kwargs["options"]["num_predict"] == 512


def test_default_model_is_gemma4_12b():
    """gemma4:12b-mlx replaced e2b as primary in June 2026 (τ2-bench 86.4% vs 29.4%)."""
    from clients.ollama_client import DEFAULT_MODEL
    assert DEFAULT_MODEL == "gemma4:12b-mlx"


def test_router_uses_cloud_model_for_agentic_tasks():
    """2026-07 revamp: Tier 1+2 automation tasks default to the cloud model
    (DeepSeek via OpenRouter) — gemma4 was proving unreliable past 2-3 chained
    tool calls. See clients/router.py module docstring and clients/cloud_client.py."""
    from clients.router import TASK_ROUTING
    from clients.cloud_client import DEFAULT_CLOUD_MODEL
    for task in ("morning_briefing", "email_pipeline", "knowledge_to_action",
                 "price_tracker", "doc_summarizer"):
        assert TASK_ROUTING[task]["model"] == DEFAULT_CLOUD_MODEL, f"{task} not on cloud model"


def test_router_model_specs_includes_12b():
    """gemma4:12b-mlx stays in MODEL_SPECS for KV/history-budget calcs even
    though it's no longer the routing default — manual override still uses it."""
    from clients.router import MODEL_SPECS
    assert "gemma4:12b-mlx" in MODEL_SPECS
    ctx, _ = MODEL_SPECS["gemma4:12b-mlx"]
    assert ctx > 0
