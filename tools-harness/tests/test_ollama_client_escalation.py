"""
Regression coverage for consecutive-tool-error escalation in
clients.ollama_client.chat() — parity with cloud_client._run_tool_loop and
local_agent_nodes.tool_node, which both bail out after 3 consecutive tool
errors instead of grinding through the remaining max_rounds. ollama_client
previously had no such guard and would burn the full round budget on a model
stuck in a tool-error loop (documented qwen3/gemma4 failure mode).

All model/tool calls are mocked — no real LLM/network calls.
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clients import ollama_client


def _tool_call(name: str = "read_file", call_id: str = "c1"):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments={}))


def _resp(content: str = "", tool_calls=None):
    return SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))


def test_three_consecutive_tool_errors_stops_early_with_partial_answer():
    mock_client = Mock()
    # Every round: model wants to call a tool. After 3 error rounds the loop
    # should break and do one final no-tools synthesis call.
    mock_client.chat.side_effect = [
        _resp(tool_calls=[_tool_call()]),  # round 0
        _resp(tool_calls=[_tool_call()]),  # round 1
        _resp(tool_calls=[_tool_call()]),  # round 2 -> 3rd consecutive error, should break
        _resp(content="Here's what I found before the errors."),  # final synthesis call
    ]

    with patch.object(ollama_client, "_get_client", return_value=mock_client), \
         patch.object(ollama_client, "execute_tool", return_value="[error] tool failed"):
        result = ollama_client.chat(
            "do the thing", tools=[{"function": {"name": "read_file"}}],
            model="qwen3:4b", max_rounds=10,
        )

    assert result == "Here's what I found before the errors."
    # 3 tool-call rounds + 1 synthesis call, not all 10 max_rounds
    assert mock_client.chat.call_count == 4


def test_successful_tool_call_resets_the_error_counter():
    mock_client = Mock()
    mock_client.chat.side_effect = [
        _resp(tool_calls=[_tool_call()]),   # round 0: error
        _resp(tool_calls=[_tool_call()]),   # round 1: success (resets counter)
        _resp(tool_calls=[_tool_call()]),   # round 2: error
        _resp(content="Final answer after mixed results."),  # round 3: done
    ]
    results = iter(["[error] failed", "ok", "[error] failed"])

    with patch.object(ollama_client, "_get_client", return_value=mock_client), \
         patch.object(ollama_client, "execute_tool", side_effect=lambda *a, **k: next(results)):
        result = ollama_client.chat(
            "do the thing", tools=[{"function": {"name": "read_file"}}],
            model="qwen3:4b", max_rounds=10,
        )

    assert result == "Final answer after mixed results."
    # Never hit 3 consecutive errors (reset at round 1), so all 4 calls happen normally
    assert mock_client.chat.call_count == 4


# --- Edge cases -------------------------------------------------------------

def test_mixed_errors_and_success_within_a_single_round_uses_last_call_processed():
    """Multiple tool calls in one round are processed in enumerate() order and
    the counter is updated per-call (mirrors cloud_client._run_tool_loop's
    per-future update) — a round ending in a successful call resets the
    streak even if earlier calls in that same round errored."""
    mock_client = Mock()
    two_calls = [_tool_call("read_file", "c1"), _tool_call("bash_exec", "c2")]
    mock_client.chat.side_effect = [
        _resp(tool_calls=two_calls),   # round 0: error, then success -> counter ends at 0
        _resp(tool_calls=two_calls),   # round 1: error, then error -> counter at 2
        _resp(tool_calls=two_calls),   # round 2: error, then error -> counter at 4, breaks after this round
        _resp(content="partial summary"),  # final synthesis call
    ]
    # round0: [error, ok], round1: [error, error], round2: [error, error]
    results = iter(["[error] x", "ok", "[error] x", "[error] x", "[error] x", "[error] x"])

    with patch.object(ollama_client, "_get_client", return_value=mock_client), \
         patch.object(ollama_client, "execute_tool", side_effect=lambda *a, **k: next(results)):
        result = ollama_client.chat(
            "do the thing", tools=[{"function": {"name": "read_file"}}],
            model="qwen3:4b", max_rounds=10,
        )

    assert result == "partial summary"
    # round0 (no break, counter reset by the trailing success), round1 (counter=2,
    # not yet >=3), round2 (counter reaches 3 mid-round on call c1, stays >=3
    # after c2) -> breaks after round2, then 1 synthesis call = 4 total
    assert mock_client.chat.call_count == 4


def test_two_consecutive_errors_then_rounds_exhausted_does_not_break_early():
    """Never reaching the 3-error threshold must not change existing
    max_rounds-exhaustion behavior — the post-loop synthesis call still runs
    because tool_calls is still truthy when the for-loop ends naturally."""
    mock_client = Mock()
    mock_client.chat.side_effect = [
        _resp(tool_calls=[_tool_call()]),  # round 0: error
        _resp(tool_calls=[_tool_call()]),  # round 1: error (only 2 consecutive, max_rounds=2 ends here)
        _resp(content="best effort after running out of rounds"),  # post-loop synthesis
    ]

    with patch.object(ollama_client, "_get_client", return_value=mock_client), \
         patch.object(ollama_client, "execute_tool", return_value="[error] tool failed"):
        result = ollama_client.chat(
            "do the thing", tools=[{"function": {"name": "read_file"}}],
            model="qwen3:4b", max_rounds=2,
        )

    assert result == "best effort after running out of rounds"
    assert mock_client.chat.call_count == 3


def test_error_detection_is_case_insensitive_and_checks_failed_keyword_too():
    """_is_error checks both 'error' and 'failed' (case-insensitive) in the
    tool result text — pin both keywords trigger the counter."""
    mock_client = Mock()
    mock_client.chat.side_effect = [
        _resp(tool_calls=[_tool_call()]),  # round 0: "FAILED" (uppercase, different keyword)
        _resp(tool_calls=[_tool_call()]),  # round 1: "Error" (mixed case)
        _resp(tool_calls=[_tool_call()]),  # round 2: 3rd consecutive -> breaks
        _resp(content="stopped after repeated failures"),
    ]
    results = iter(["Operation FAILED unexpectedly", "Error: bad input", "failed again"])

    with patch.object(ollama_client, "_get_client", return_value=mock_client), \
         patch.object(ollama_client, "execute_tool", side_effect=lambda *a, **k: next(results)):
        result = ollama_client.chat(
            "do the thing", tools=[{"function": {"name": "read_file"}}],
            model="qwen3:4b", max_rounds=10,
        )

    assert result == "stopped after repeated failures"
    assert mock_client.chat.call_count == 4
