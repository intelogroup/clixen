"""
tests/test_tool_progress.py
Verify that on_token receives progress labels BEFORE tool results
in both cloud_client and ollama_client tool loops.
"""
import json
from unittest.mock import MagicMock, patch
from types import SimpleNamespace


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_cloud_msg(tool_name: str, call_id: str = "c1"):
    """Fake streamed OpenAI ChatCompletion chunks with one tool call delta."""
    tc_delta = SimpleNamespace(
        index=0, id=call_id,
        function=SimpleNamespace(name=tool_name, arguments='{"query": "test"}'),
    )
    delta = SimpleNamespace(content=None, tool_calls=[tc_delta])
    chunk = SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)
    usage_chunk = SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
    return [chunk, usage_chunk]


def _make_cloud_final(text: str = "All done."):
    """Fake streamed final (no-tool-calls) chunks."""
    delta = SimpleNamespace(content=text, tool_calls=None)
    chunk = SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)
    usage_chunk = SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
    return [chunk, usage_chunk]


# ── cloud_client tests ─────────────────────────────────────────────────────────

def test_cloud_progress_label_emitted_before_result():
    """Progress label must appear in tokens BEFORE the final synthesis text."""
    from clients import cloud_client

    tokens: list[str] = []

    fake_client = MagicMock()
    # Round 0: tool call; Round 1 (final): plain answer
    fake_client.chat.completions.create.side_effect = [
        _make_cloud_msg("ask_web_search"),
        _make_cloud_final("Here are the results."),
    ]

    with patch.object(cloud_client, "_resolve", return_value=(fake_client, "deepseek-v4-flash")), \
         patch.object(cloud_client, "execute_tool", return_value="search result"), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "_record_trace"), \
         patch.object(cloud_client, "check_aborted"), \
         patch.object(cloud_client, "check_budget"), \
         patch.object(cloud_client, "wrap_external_output", side_effect=lambda n, r: r):

        cloud_client._run_tool_loop(
            model="deepseek/deepseek-v4-flash",
            messages=[{"role": "user", "content": "search for python 3.13"}],
            tools=[],
            on_token=tokens.append,
            max_rounds=5,
        )

    # At least one progress token must exist
    progress = [t for t in tokens if "🔍" in t or "Searching" in t]
    assert progress, f"No progress label found in tokens: {tokens}"

    # Final synthesis text must also be in tokens
    final = [t for t in tokens if "Here are the results" in t]
    assert final, f"Final answer not in tokens: {tokens}"

    # Progress must precede final text
    first_progress_idx = next(i for i, t in enumerate(tokens) if "🔍" in t or "Searching" in t)
    first_final_idx = next(i for i, t in enumerate(tokens) if "Here are the results" in t)
    assert first_progress_idx < first_final_idx, (
        f"Progress label (idx={first_progress_idx}) must precede final answer "
        f"(idx={first_final_idx}). tokens={tokens}"
    )


def test_cloud_unknown_tool_gets_generic_label():
    """An unknown tool name gets the ⚙️ Running <name>… fallback."""
    from clients import cloud_client

    tokens: list[str] = []

    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = [
        _make_cloud_msg("some_custom_tool"),
        _make_cloud_final("done"),
    ]

    with patch.object(cloud_client, "_resolve", return_value=(fake_client, "deepseek-v4-flash")), \
         patch.object(cloud_client, "execute_tool", return_value="ok"), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "_record_trace"), \
         patch.object(cloud_client, "check_aborted"), \
         patch.object(cloud_client, "check_budget"), \
         patch.object(cloud_client, "wrap_external_output", side_effect=lambda n, r: r):

        cloud_client._run_tool_loop(
            model="deepseek/deepseek-v4-flash",
            messages=[{"role": "user", "content": "run custom"}],
            tools=[],
            on_token=tokens.append,
            max_rounds=5,
        )

    generic = [t for t in tokens if "some_custom_tool" in t]
    assert generic, f"Generic fallback label not found in tokens: {tokens}"


def test_cloud_no_on_token_no_crash():
    """When on_token=None, progress labels are simply skipped — no AttributeError."""
    from clients import cloud_client

    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = [
        _make_cloud_msg("ask_web_search"),
        _make_cloud_final("done"),
    ]

    with patch.object(cloud_client, "_resolve", return_value=(fake_client, "deepseek-v4-flash")), \
         patch.object(cloud_client, "execute_tool", return_value="ok"), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "_record_trace"), \
         patch.object(cloud_client, "check_aborted"), \
         patch.object(cloud_client, "check_budget"), \
         patch.object(cloud_client, "wrap_external_output", side_effect=lambda n, r: r):

        result = cloud_client._run_tool_loop(
            model="deepseek/deepseek-v4-flash",
            messages=[{"role": "user", "content": "x"}],
            tools=[],
            on_token=None,
            max_rounds=5,
        )
    assert result == "done"


# ── ollama_client tests ────────────────────────────────────────────────────────

def _make_ollama_stream_result(tool_name: str):
    """Return (content, tool_calls, elapsed, msg) as _run_streaming would."""
    call = SimpleNamespace(
        function=SimpleNamespace(name=tool_name, arguments={"query": "test"})
    )
    msg = SimpleNamespace(content="", tool_calls=[call], role="assistant")
    return ("", [call], 0.1, msg)


def _make_ollama_local_final(text: str = "Final answer."):
    """Return (OllamaResp, elapsed) as _run_local would for the synthesis round."""
    msg = SimpleNamespace(content=text, tool_calls=None, role="assistant")
    resp = SimpleNamespace(message=msg)
    return (resp, 0.1)


def test_ollama_progress_label_emitted():
    """ollama_client.chat emits progress label before tool result.

    Round 0: on_token is set + no tool results in history → _run_streaming path.
    Round 1: tool results are in history → _run_local path (synthesis).
    """
    from clients import ollama_client

    tokens: list[str] = []

    with patch.object(ollama_client, "_run_streaming", return_value=_make_ollama_stream_result("ask_email_agent")), \
         patch.object(ollama_client, "_run_local", return_value=_make_ollama_local_final("Here's your email summary.")), \
         patch.object(ollama_client, "_run_tool_gated", return_value="email result"), \
         patch.object(ollama_client, "check_aborted"), \
         patch.object(ollama_client, "wrap_external_output", side_effect=lambda n, r: r), \
         patch.object(ollama_client, "_get_client"):

        ollama_client.chat(
            user_message="check my email",
            tools=[],
            on_token=tokens.append,
            max_rounds=5,
        )

    progress = [t for t in tokens if "📧" in t or "Checking email" in t]
    assert progress, f"Email progress label missing from tokens: {tokens}"


def test_ollama_label_dict_keys_match_cloud():
    """Both clients must expose the same set of tool names in their label dicts."""
    from clients.cloud_client import _TOOL_PROGRESS_LABELS as cloud_labels
    from clients.ollama_client import _TOOL_PROGRESS_LABELS as ollama_labels
    assert set(cloud_labels.keys()) == set(ollama_labels.keys()), (
        f"Label dict mismatch.\n"
        f"Only in cloud: {set(cloud_labels) - set(ollama_labels)}\n"
        f"Only in ollama: {set(ollama_labels) - set(cloud_labels)}"
    )
