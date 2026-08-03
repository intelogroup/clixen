"""
Mid-run plan checkpoint (clients/cloud_client.py's `_run_tool_loop`): once past
the halfway point of max_rounds, if store/plan_store has an unfinished plan for
this run_id, inject one system nudge listing pending steps. Generalizes
harness.py's post-answer verify-on-absence/claim-check to a mid-loop check, so
drift gets caught before the round budget runs out instead of after.

All model calls mocked — no real LLM/network calls.
"""
from types import SimpleNamespace
from unittest.mock import patch

from clients import cloud_client
from store import plan_store


def _msg(content: str = "", tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=None,
    )


def _tool_call(name: str = "ask_run_command", call_id: str = "c1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments="{}"))


def test_checkpoint_injected_once_when_plan_incomplete():
    rid = "checkpoint-test-run-1"
    plan_store.set_plan(rid, ["step a", "step b", "step c"])

    messages = [{"role": "user", "content": "do three things"}]
    responses = [
        _msg(tool_calls=[_tool_call(call_id="c1")]),  # round 0
        _msg(tool_calls=[_tool_call(call_id="c2")]),  # round 1
        _msg(tool_calls=[_tool_call(call_id="c3")]),  # round 2 (>= max_rounds//2 == 2 -> checkpoint fires here)
        _msg(content="done"),                          # round 3
    ]
    with patch.object(cloud_client, "_resolve", return_value=(object(), "deepseek-v4-flash")), \
         patch.object(cloud_client, "_stream_completion", side_effect=responses), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "check_budget"), \
         patch.object(cloud_client, "execute_tool", return_value="ok"):
        cloud_client._run_tool_loop(
            "deepseek/deepseek-v4-flash", messages, [], None, max_rounds=4, run_id=rid,
        )

    checkpoint_msgs = [m for m in messages if m.get("role") == "system" and "Checkpoint:" in m.get("content", "")]
    assert len(checkpoint_msgs) == 1
    assert "step a" in checkpoint_msgs[0]["content"]
    assert "step c" in checkpoint_msgs[0]["content"]


def test_no_checkpoint_when_plan_complete():
    rid = "checkpoint-test-run-2"
    plan_store.set_plan(rid, ["step a"])
    plan_store.mark_done(rid, [0])

    messages = [{"role": "user", "content": "do one thing"}]
    responses = [
        _msg(tool_calls=[_tool_call(call_id="c1")]),
        _msg(tool_calls=[_tool_call(call_id="c2")]),
        _msg(tool_calls=[_tool_call(call_id="c3")]),
        _msg(content="done"),
    ]
    with patch.object(cloud_client, "_resolve", return_value=(object(), "deepseek-v4-flash")), \
         patch.object(cloud_client, "_stream_completion", side_effect=responses), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "check_budget"), \
         patch.object(cloud_client, "execute_tool", return_value="ok"):
        cloud_client._run_tool_loop(
            "deepseek/deepseek-v4-flash", messages, [], None, max_rounds=4, run_id=rid,
        )

    assert not any(m.get("role") == "system" and "Checkpoint:" in m.get("content", "") for m in messages)


def test_no_checkpoint_when_no_run_id():
    messages = [{"role": "user", "content": "hi"}]
    responses = [_msg(content="ok")]
    with patch.object(cloud_client, "_resolve", return_value=(object(), "deepseek-v4-flash")), \
         patch.object(cloud_client, "_stream_completion", side_effect=responses), \
         patch.object(cloud_client, "_track_usage"), \
         patch.object(cloud_client, "check_budget"):
        cloud_client._run_tool_loop(
            "deepseek/deepseek-v4-flash", messages, [], None, max_rounds=4, run_id=None,
        )
    assert not any(m.get("role") == "system" and "Checkpoint:" in m.get("content", "") for m in messages)
