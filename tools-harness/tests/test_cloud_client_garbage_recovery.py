"""
Regression coverage for the DSML-leak / empty-response recovery guard in
clients.cloud_client._run_tool_loop.

Confirmed live 2026-07-07/08 on a real Telegram user's session: DeepSeek
occasionally emits a malformed pseudo tool-call as raw text (containing the
"｜｜" marker) or an empty response instead of a real answer. A guard existed
for this on the per-round path (round produced no tool_calls), but the
*separate* final completion after max_rounds is exhausted — used when the
model still wants to call tools but the round budget ran out — returned raw
model output completely unchecked. Every DSML leak found in the real
transcript correlated with a run that hit the round cap. Fixed by routing
both paths through the shared `_recover_from_garbage` helper.

All model calls are mocked — no real LLM/network calls.
"""
from types import SimpleNamespace
from unittest.mock import patch

from clients import cloud_client


def _msg(content: str = "", tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=None,
    )


def _tool_call(name: str = "ask_run_command", call_id: str = "c1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments="{}"),
    )


def _tool_schema(name: str):
    return [{"type": "function", "function": {"name": name}}]


def test_round0_garbage_still_recovers_via_nudge():
    """Regression: the original round-0 guard behavior must be unchanged."""
    responses = [
        _msg(content="<｜｜DSML｜｜tool_calls>garbage"),  # round 0: garbage, no tool_calls
        _msg(content="Here is a clean answer."),           # nudge attempt 1: recovers
    ]
    with patch.object(cloud_client, "_resolve", return_value=(object(), "deepseek-v4-flash")), \
         patch.object(cloud_client, "_stream_completion", side_effect=responses), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "check_budget"):
        result = cloud_client._run_tool_loop(
            "deepseek/deepseek-v4-flash", [{"role": "user", "content": "hi"}], [], None, max_rounds=8,
        )
    assert result == "Here is a clean answer."


def test_max_rounds_exhaustion_garbage_now_recovers_via_nudge():
    """The bug: previously this path returned raw garbage unchecked."""
    responses = [
        _msg(tool_calls=[_tool_call()]),                    # round 0: wants a tool call
        # final forced completion (tools=None) after max_rounds exhausted, still mid-task:
        _msg(content="<｜｜DSML｜｜invoke name=\"ask_read_file\">garbage"),
        _msg(content="Here is the file summary."),          # nudge attempt 1: recovers
    ]
    with patch.object(cloud_client, "_resolve", return_value=(object(), "deepseek-v4-flash")), \
         patch.object(cloud_client, "_stream_completion", side_effect=responses), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "check_budget"), \
         patch.object(cloud_client, "execute_tool", return_value="ok"):
        result = cloud_client._run_tool_loop(
            "deepseek/deepseek-v4-flash", [{"role": "user", "content": "do the thing"}], _tool_schema("ask_run_command"), None, max_rounds=1,
        )
    assert result == "Here is the file summary."
    assert "｜｜" not in result


def test_max_rounds_exhaustion_fails_closed_if_nudges_dont_help():
    responses = [
        _msg(tool_calls=[_tool_call()]),  # round 0: wants a tool call
        _msg(content=""),                 # final forced completion: empty
        _msg(content=""),                 # nudge 1: still empty
        _msg(content=""),                 # nudge 2: still empty
    ]
    with patch.object(cloud_client, "_resolve", return_value=(object(), "deepseek-v4-flash")), \
         patch.object(cloud_client, "_stream_completion", side_effect=responses), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "check_budget"), \
         patch.object(cloud_client, "execute_tool", return_value="ok"):
        result = cloud_client._run_tool_loop(
            "deepseek/deepseek-v4-flash", [{"role": "user", "content": "do the thing"}], _tool_schema("ask_run_command"), None, max_rounds=1,
        )
    assert result.startswith("I ran into trouble completing that.")
    assert "ask_run_command" in result
    assert "ok" in result


def test_fails_closed_message_names_last_tool_step():
    """Fallback message should tell the user what the last tool step did,
    not just apologize with no information — added 2026-07-09 after a real
    Telegram user hit this twice on a PDF-then-email chain with no clue
    whether the PDF was created or the email was sent."""
    responses = [
        _msg(tool_calls=[_tool_call(name="create_pdf", call_id="c1")]),
        _msg(content=""),  # final forced completion: empty
        _msg(content=""),  # nudge 1
        _msg(content=""),  # nudge 2
    ]
    with patch.object(cloud_client, "_resolve", return_value=(object(), "deepseek-v4-flash")), \
         patch.object(cloud_client, "_stream_completion", side_effect=responses), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "check_budget"), \
         patch.object(cloud_client, "execute_tool", return_value="Created /tmp/report.pdf"):
        result = cloud_client._run_tool_loop(
            "deepseek/deepseek-v4-flash", [{"role": "user", "content": "make a pdf"}], _tool_schema("create_pdf"), None, max_rounds=1,
        )
    assert "create_pdf" in result
    assert "/tmp/report.pdf" in result


def test_generic_special_token_leak_also_triggers_nudge():
    """Not just DeepSeek's ｜｜ marker — <|special_token|>, <tool_calls>,
    <invoke ...>, and [TOOL_CALLS] shapes (Llama/Mistral/Qwen chat templates)
    must all be recognized as leaks, not just the one format that happened
    to be observed first."""
    for garbage in [
        "<|reserved_token_5|>some leak text",
        "<tool_calls>garbage</tool_calls>",
        '<invoke name="ask_read_file">garbage',
        "[TOOL_CALLS]garbage",
    ]:
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=garbage, tool_calls=None))], usage=None),
            _msg(content="Here is a clean answer."),
        ]
        with patch.object(cloud_client, "_resolve", return_value=(object(), "deepseek-v4-flash")), \
             patch.object(cloud_client, "_stream_completion", side_effect=responses), \
              patch.object(cloud_client, "_track_usage"), \
              patch.object(cloud_client, "check_budget"):
            result = cloud_client._run_tool_loop(
                "deepseek/deepseek-v4-flash", [{"role": "user", "content": "hi"}], [], None, max_rounds=8,
            )
        assert result == "Here is a clean answer.", f"failed to catch leak shape: {garbage!r}"


def test_leak_alongside_valid_tool_call_stripped_before_history():
    """A round can have a real tool_calls AND a leaked token in the same
    message's content. That content used to get appended to `messages`
    (conversation history) completely unscrubbed — confirmed corrupting the
    rolling conversation-history summary in production (store.conversation
    'Fold ... looked corrupted' retries, 2026-07-09). Must be stripped before
    it enters `messages`, independent of whether the round otherwise succeeds."""
    responses = [
        _msg(content="<｜｜DSML｜｜>stray leak", tool_calls=[_tool_call(name="ask_run_command", call_id="c1")]),
        _msg(content="Done."),
    ]
    with patch.object(cloud_client, "_resolve", return_value=(object(), "deepseek-v4-flash")), \
         patch.object(cloud_client, "_stream_completion", side_effect=responses), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "check_budget"), \
         patch.object(cloud_client, "execute_tool", return_value="ok"):
        messages = [{"role": "user", "content": "do the thing"}]
        cloud_client._run_tool_loop(
            "deepseek/deepseek-v4-flash", messages, [], None, max_rounds=8,
        )
    stored_assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
    assert stored_assistant_msgs, "expected the tool-call round to be recorded in history"
    assert "｜｜" not in stored_assistant_msgs[0]["content"]


def test_max_rounds_exhaustion_clean_content_passes_through_unchanged():
    """No garbage at all — the common case — must still work exactly as before."""
    responses = [
        _msg(tool_calls=[_tool_call()]),               # round 0: wants a tool call
        _msg(content="Done, here's what I found."),    # final forced completion: clean
    ]
    with patch.object(cloud_client, "_resolve", return_value=(object(), "deepseek-v4-flash")), \
         patch.object(cloud_client, "_stream_completion", side_effect=responses), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "check_budget"), \
         patch.object(cloud_client, "execute_tool", return_value="ok"):
        result = cloud_client._run_tool_loop(
            "deepseek/deepseek-v4-flash", [{"role": "user", "content": "do the thing"}], [], None, max_rounds=1,
        )
    assert result == "Done, here's what I found."
