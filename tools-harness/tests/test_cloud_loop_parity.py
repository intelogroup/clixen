"""Characterization tests pinning _run_tool_loop behavior BEFORE the
_LoopState/one_round extraction (plan M1, "extract, don't rewrite").

These drive the PUBLIC _run_tool_loop with a fake _stream_completion +
execute_tool, so they keep passing verbatim after the loop body is factored
into _LoopState + one_round(). If a refactor breaks one of these, behavior
changed — the extraction is wrong, not the test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clients.cloud_client as cc


def _tool_call_resp(name, args, cid="t1"):
    tc = SimpleNamespace(
        id=cid,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )
    msg = SimpleNamespace(content="", tool_calls=[tc])
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def _text_resp(content):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def _patch(monkeypatch, responses, tool_result="tool output"):
    """Script responses for _stream_completion; return the call recorder."""
    calls = {"exec": [], "stream": [], "trace": [], "budget": 0, "abort": 0,
             "resolved": []}
    seq = list(responses)

    def _stream(client, on_token, **kw):
        calls["stream"].append(kw)
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(cc, "_stream_completion", _stream)
    monkeypatch.setattr(cc, "execute_tool",
                        lambda name, args: (calls["exec"].append((name, args)),
                                            tool_result)[1])
    monkeypatch.setattr(cc, "_record_trace",
                        lambda rid, entry: calls["trace"].append(entry))
    monkeypatch.setattr(cc, "_reasoning_extra_body", lambda *a, **k: {})
    monkeypatch.setattr(cc, "_recover_from_garbage",
                        lambda client, model, on_token, messages, content: content)
    monkeypatch.setattr(cc, "_track_usage", lambda *a, **k: None)
    monkeypatch.setattr(cc, "check_aborted", lambda: calls.__setitem__("abort", calls["abort"] + 1))
    monkeypatch.setattr(cc, "check_budget", lambda: calls.__setitem__("budget", calls["budget"] + 1))
    monkeypatch.setattr(cc, "_resolve",
                        lambda m: (calls["resolved"].append(m) or None, m))
    return calls


_TOOLS = [{"type": "function", "function": {"name": "get_current_time"}},
          {"type": "function", "function": {"name": "web_search"}}]


def test_tool_round_then_answer_shape(monkeypatch):
    calls = _patch(monkeypatch, [
        _tool_call_resp("get_current_time", {"tz": "UTC"}),
        _text_resp("It is noon."),
    ])
    msgs = [{"role": "user", "content": "time?"}]
    out = cc._run_tool_loop("deepseek/deepseek-v4-flash", msgs, tools=_TOOLS,
                            on_token=None, max_rounds=5, run_id="r1")
    assert out == "It is noon."
    assert calls["exec"] == [("get_current_time", {"tz": "UTC"})]
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool"]
    assert msgs[1]["tool_calls"][0]["function"]["name"] == "get_current_time"
    assert msgs[2]["tool_call_id"] == "t1"
    assert any(t["tool"] == "get_current_time" and t["error"] is False
               for t in calls["trace"]), calls["trace"]


def test_budget_and_abort_checked_every_round(monkeypatch):
    calls = _patch(monkeypatch, [
        _tool_call_resp("web_search", {"q": "a"}),
        _tool_call_resp("web_search", {"q": "b"}, cid="t2"),
        _text_resp("done"),
    ])
    cc._run_tool_loop("deepseek/deepseek-v4-flash",
                      [{"role": "user", "content": "go"}], tools=_TOOLS,
                      on_token=None, max_rounds=5, run_id="r2")
    assert calls["stream"] and calls["budget"] >= 3
    assert calls["abort"] >= 3


def test_unoffered_tool_is_rejected_without_execution(monkeypatch):
    calls = _patch(monkeypatch, [
        _tool_call_resp("delete_everything", {"x": 1}),
        _text_resp("ok"),
    ], tool_result="should not run")
    msgs = [{"role": "user", "content": "go"}]
    cc._run_tool_loop("deepseek/deepseek-v4-flash", msgs, tools=_TOOLS,
                      on_token=None, max_rounds=5, run_id="r3")
    assert calls["exec"] == [], "un-offered tool must never execute"
    assert "not offered this round" in msgs[2]["content"]


def test_escalates_after_three_consecutive_tool_errors(monkeypatch):
    calls = _patch(monkeypatch, [
        _tool_call_resp("web_search", {"q": "a"}, cid="t1"),
        _tool_call_resp("web_search", {"q": "b"}, cid="t2"),
        _tool_call_resp("web_search", {"q": "c"}, cid="t3"),
        _text_resp("gave up"),
    ], tool_result="[error] boom")
    msgs = [{"role": "user", "content": "go"}]
    cc._run_tool_loop("deepseek/deepseek-v4-flash", msgs, tools=_TOOLS,
                      on_token=None, max_rounds=6, run_id="r4",
                      fallback_model="openai/gpt-4.1-mini")
    assert len(calls["exec"]) == 3, calls["exec"]
    assert "openai/gpt-4.1-mini" in calls["resolved"], calls["resolved"]
    esc = [t for t in calls["trace"] if t["tool"] == "_model_escalation"]
    assert len(esc) == 1, calls["trace"]
    assert esc[0]["args"] == {"from": "deepseek/deepseek-v4-flash",
                              "to": "openai/gpt-4.1-mini"}
    assert esc[0]["escalated"] is True


def test_post_loop_synthesis_when_rounds_exhausted(monkeypatch):
    # Every round ends in a tool call; after max_rounds the loop does one final
    # synthesis call with tools=None.
    calls = _patch(monkeypatch, [
        _tool_call_resp("web_search", {"q": "a"}),
        _text_resp("final synthesis"),
    ])
    msgs = [{"role": "user", "content": "go"}]
    out = cc._run_tool_loop("deepseek/deepseek-v4-flash", msgs, tools=_TOOLS,
                            on_token=None, max_rounds=1, run_id="r5")
    assert out == "final synthesis"
    last = calls["stream"][-1]
    assert last.get("tools") is None, "synthesis call must not offer tools"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
