import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clients.ollama_client import chat


def _resp(content="", tool_calls=None):
    return SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))


def test_chat_executes_text_encoded_tool_call_and_returns_followup_answer():
    tools = [{"function": {"name": "web_search"}}]

    responses = [
        _resp('{"name":"web_search","arguments":{"query":"bitcoin price"}}'),
        _resp("Bitcoin is trading at $94,000."),
    ]

    with patch("clients.ollama_client._run_local", side_effect=[(responses[0], 0.01), (responses[1], 0.01)]), \
         patch("clients.ollama_client.execute_tool", return_value="search results") as mock_exec, \
         patch("clients.ollama_client._premise_inject", side_effect=lambda user_message, result: result) as mock_inject:
        result = chat("what is bitcoin price today?", tools=tools, model="qwen3:4b")

    assert result == "Bitcoin is trading at $94,000."
    mock_exec.assert_called_once_with("web_search", {"query": "bitcoin price"})
    mock_inject.assert_called_once_with("what is bitcoin price today?", "search results")


def test_chat_truncates_large_tool_results_in_followup_round():
    tool_call = SimpleNamespace(function=SimpleNamespace(name="read_file", arguments={"path": "demo.txt"}))
    responses = [
        _resp("", [tool_call]),
        _resp("done"),
    ]
    large_result = "x" * 5001
    captured_messages = []

    def _fake_run_local(model, messages, tools, temperature=None, **kwargs):
        captured_messages.append(messages)
        idx = len(captured_messages) - 1
        return responses[idx], 0.01

    with patch("clients.ollama_client._run_local", side_effect=_fake_run_local), \
         patch("clients.ollama_client.execute_tool", return_value=large_result):
        result = chat("open the file", tools=[{"function": {"name": "read_file"}}], model="qwen3:8b")

    assert result == "done"
    tool_messages = [m for m in captured_messages[-1] if isinstance(m, dict) and m.get("role") == "tool"]
    assert tool_messages, "expected a tool result message in the followup round"
    assert "[truncated" in tool_messages[0]["content"]


def test_chat_streams_blocking_synthesis_after_tool_round():
    tool_call = SimpleNamespace(function=SimpleNamespace(name="read_file", arguments={"path": "demo.txt"}))
    streamed = []

    with patch("clients.ollama_client._run_streaming", return_value=("", [tool_call], 0.01, SimpleNamespace(role="assistant", content="", tool_calls=[tool_call]))), \
         patch("clients.ollama_client._run_local", return_value=(_resp("final answer"), 0.01)), \
         patch("clients.ollama_client.execute_tool", return_value="file contents"):
        result = chat(
            "read the file",
            tools=[{"function": {"name": "read_file"}}],
            model="qwen3:8b",
            on_token=streamed.append,
        )

    assert result == "final answer"
    # Progress-indicator token: emitted before blocking tool execution so the UI shows
    # immediate feedback (ollama_client.py's _TOOL_PROGRESS_LABELS / _run_tool_gated flow).
    # "read_file" has no friendly label, so it falls back to the generic format.
    assert streamed == ["\n\n⚙️ Running read_file…", "final answer"]
