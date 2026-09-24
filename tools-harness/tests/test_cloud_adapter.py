"""Tests for agents/cloud_adapter.py — run_loop's model_fn backed by the real
cloud model via cloud_client.raw_completion (public single-completion seam; the
internal chat() loop is deliberately NOT used — run_loop owns tool execution).

raw_completion is monkeypatched, so no provider calls happen here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clients.cloud_client as cc
from agents import cloud_adapter


def _choice(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(message=msg)


def _tc(name, args_json, cid="c1"):
    return SimpleNamespace(id=cid,
                           function=SimpleNamespace(name=name, arguments=args_json))


def _patch_raw(monkeypatch, choice):
    """Fake with cloud_client.raw_completion's EXACT signature — a permissive
    **kw fake would hide adapter bugs like passing a kwarg the real function
    does not accept."""
    import inspect
    real = inspect.signature(cc.raw_completion)
    seen = {"kwargs": None}

    def fake(model, messages, tools, **kw):
        # hard-fail on anything the real function would reject
        unknown = set(kw) - set(real.parameters)
        assert not unknown, f"raw_completion has no parameter(s) {unknown}"
        seen["kwargs"] = {"model": model, "tools": tools, **kw}
        return choice

    monkeypatch.setattr(cc, "raw_completion", fake)
    return seen


def test_text_only_response_maps_to_run_loop_shape(monkeypatch):
    _patch_raw(monkeypatch, _choice(content="hello there"))
    fn = cloud_adapter.make_model_fn("deepseek/deepseek-v4-flash", tools=[])
    out = fn([{"role": "user", "content": "hi"}], [])
    assert out == {"text": "hello there", "tool_calls": []}


def test_tool_calls_parse_arguments_to_dicts(monkeypatch):
    _patch_raw(monkeypatch, _choice(content="", tool_calls=[
        _tc("get_current_time", json.dumps({"tz": "UTC"}), cid="c1"),
        _tc("web_search", json.dumps({"q": "clixen"}), cid="c2"),
    ]))
    fn = cloud_adapter.make_model_fn("deepseek/deepseek-v4-flash", tools=[])
    out = fn([{"role": "user", "content": "time?"}], ["get_current_time", "web_search"])
    assert out["tool_calls"] == [
        {"id": "c1", "name": "get_current_time", "args": {"tz": "UTC"}},
        {"id": "c2", "name": "web_search", "args": {"q": "clixen"}},
    ]


def test_malformed_arguments_become_empty_dict(monkeypatch):
    _patch_raw(monkeypatch, _choice(content="", tool_calls=[
        _tc("web_search", "{not json", cid="c1"),
    ]))
    fn = cloud_adapter.make_model_fn("deepseek/deepseek-v4-flash", tools=[])
    out = fn([{"role": "user", "content": "go"}], ["web_search"])
    assert out["tool_calls"][0]["args"] == {}


def test_only_requested_tool_schemas_are_offered(monkeypatch):
    seen = _patch_raw(monkeypatch, _choice(content="done"))
    schemas = [
        {"type": "function", "function": {"name": "get_current_time"}},
        {"type": "function", "function": {"name": "delete_everything"}},
    ]
    fn = cloud_adapter.make_model_fn("deepseek/deepseek-v4-flash", tools=schemas)
    fn([{"role": "user", "content": "time?"}], ["get_current_time"])
    offered = [t["function"]["name"] for t in seen["kwargs"]["tools"]]
    assert offered == ["get_current_time"], offered


def test_no_tools_means_no_tools_param(monkeypatch):
    seen = _patch_raw(monkeypatch, _choice(content="done"))
    fn = cloud_adapter.make_model_fn("deepseek/deepseek-v4-flash", tools=[])
    fn([{"role": "user", "content": "hi"}], [])
    assert seen["kwargs"]["tools"] in (None, [])


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
