"""
Regression test for the offline-fallback added to harness.local_chat() and
agents.local_agent_nodes._chat(): when the cloud path is entirely unreachable
(no network, missing API key, provider outage), both dispatchers must fall
back to local ollama instead of raising.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

import harness as h
from clients import cloud_client, ollama_client
from agents import local_agent_nodes as lan


def test_local_chat_falls_back_to_ollama_when_cloud_unreachable():
    with patch.object(cloud_client, "chat", side_effect=RuntimeError("OPENROUTER_API_KEY is not set")), \
         patch.object(ollama_client, "chat", return_value="local answer") as mock_ollama:
        result = h.local_chat(user_message="hi", model=cloud_client.DEFAULT_CLOUD_MODEL)
    mock_ollama.assert_called_once()
    assert mock_ollama.call_args.kwargs["model"] == ollama_client.DEFAULT_MODEL
    assert result == "local answer"


def test_local_agent_chat_falls_back_to_ollama_when_cloud_unreachable():
    with patch("clients.cloud_client.raw_completion", side_effect=ConnectionError("network down")), \
         patch.object(lan, "_ollama_chat", return_value="local response") as mock_ollama:
        result = lan._chat(cloud_client.DEFAULT_CLOUD_MODEL, [{"role": "user", "content": "hi"}], [])
    mock_ollama.assert_called_once_with(
        lan._LOCAL_DEFAULT_MODEL, [{"role": "user", "content": "hi"}], [], temperature=0.0,
    )
    assert result == "local response"


def test_local_chat_bypasses_budget_instead_of_falling_back_to_local():
    with patch.object(
        cloud_client, "chat",
        side_effect=[cloud_client.BudgetExceededError("over budget"), "cloud answer (bypassed)"],
    ) as mock_chat:
        result = h.local_chat(user_message="hi", model=cloud_client.DEFAULT_CLOUD_MODEL)
    assert mock_chat.call_count == 2
    assert mock_chat.call_args_list[1].kwargs["bypass_budget"] is True
    assert result == "cloud answer (bypassed)"
