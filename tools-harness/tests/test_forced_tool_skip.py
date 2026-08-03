"""#3 latency: force_tool_choice skips the model-driven round0.

Proves the forced tool is executed directly (one tool call, one model call for
synthesis — not two model calls), the trace is recorded under run_id so the
reliability machinery still sees it, and the pre-seed is idempotent across
chat()'s fallback-retry (which reuses the mutated messages list).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import clients.cloud_client as cc


def _fake_synthesis_resp(content):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def _patch(monkeyed):
    """Return (calls, restore). Records execute_tool + _stream_completion calls."""
    calls = {"exec": [], "stream": 0, "trace": []}
    orig = {
        "exec": cc.execute_tool, "stream": cc._stream_completion,
        "trace": cc._record_trace, "resolve": cc._resolve,
        "reason": cc._reasoning_extra_body, "recover": cc._recover_from_garbage,
        "abort": cc.check_aborted, "track": cc._track_usage,
    }
    cc.execute_tool = lambda name, args: (calls["exec"].append((name, args)), "Italy won Euro 2020.")[1]
    def _stream(client, on_token, **kw):
        calls["stream"] += 1
        return _fake_synthesis_resp("Italy won.")
    cc._stream_completion = _stream
    cc._record_trace = lambda rid, entry: calls["trace"].append(entry)
    cc._resolve = lambda m: (None, m)
    cc._reasoning_extra_body = lambda *a, **k: {}
    cc._recover_from_garbage = lambda client, model, on_token, messages, content: content
    cc.check_aborted = lambda: None
    cc._track_usage = lambda *a, **k: None
    def restore():
        cc.execute_tool, cc._stream_completion, cc._record_trace = orig["exec"], orig["stream"], orig["trace"]
        cc._resolve, cc._reasoning_extra_body, cc._recover_from_garbage = orig["resolve"], orig["reason"], orig["recover"]
        cc.check_aborted, cc._track_usage = orig["abort"], orig["track"]
    return calls, restore


def test_forced_tool_executed_directly_one_model_call():
    calls, restore = _patch(None)
    try:
        msgs = [{"role": "user", "content": "who won the euro 2020"}]
        out = cc._run_tool_loop(
            "deepseek/deepseek-v4-flash", msgs, tools=[{"x": 1}], on_token=None,
            max_rounds=8, run_id="rid123", force_tool_choice="ask_research_agent",
        )
    finally:
        restore()
    assert calls["exec"] == [("ask_research_agent", {"query": "who won the euro 2020"})], calls["exec"]
    assert calls["stream"] == 1, f"expected 1 synthesis call, got {calls['stream']} (round0 not skipped)"
    assert any(t["tool"] == "ask_research_agent" for t in calls["trace"]), calls["trace"]
    assert out == "Italy won."
    # messages seeded: user, assistant tool_call, tool result
    assert msgs[1]["role"] == "assistant" and msgs[1]["tool_calls"][0]["function"]["name"] == "ask_research_agent"
    assert msgs[2]["role"] == "tool"


def test_deepseek_thinking_gets_reasoning_content():
    # deepseek thinking models 400 without reasoning_content on the fabricated turn;
    # the synthetic assistant message must carry it — and only for those models.
    _, restore = _patch(None)
    try:
        msgs = [{"role": "user", "content": "weather in paris"}]
        cc._run_tool_loop(
            "deepseek/deepseek-v4-flash", msgs, tools=[{"x": 1}], on_token=None,
            max_rounds=8, run_id="rid789", force_tool_choice="ask_web_search",
        )
    finally:
        restore()
    assert "reasoning_content" in msgs[1], f"thinking model needs reasoning_content: {msgs[1]}"

    _, restore = _patch(None)
    try:
        msgs = [{"role": "user", "content": "weather in paris"}]
        cc._run_tool_loop(
            "deepseek/deepseek-chat", msgs, tools=[{"x": 1}], on_token=None,
            max_rounds=8, run_id="rid790", force_tool_choice="ask_web_search",
        )
    finally:
        restore()
    assert "reasoning_content" not in msgs[1], f"non-thinking must omit it: {msgs[1]}"


def test_idempotent_when_tool_already_seeded():
    # simulates chat()'s retry: messages already carry a tool result
    calls, restore = _patch(None)
    try:
        msgs = [
            {"role": "user", "content": "who won"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "x", "type": "function", "function": {"name": "ask_research_agent", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "x", "content": "Italy won."},
        ]
        cc._run_tool_loop(
            "deepseek/deepseek-v4-flash", msgs, tools=[{"x": 1}], on_token=None,
            max_rounds=8, run_id="rid456", force_tool_choice="ask_research_agent",
        )
    finally:
        restore()
    assert calls["exec"] == [], f"pre-seed must not re-execute on retry: {calls['exec']}"


if __name__ == "__main__":
    test_forced_tool_executed_directly_one_model_call()
    test_deepseek_thinking_gets_reasoning_content()
    test_idempotent_when_tool_already_seeded()
    print("ok")
