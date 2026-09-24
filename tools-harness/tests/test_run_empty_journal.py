"""Regression: a run whose journal has no user message must fail FAST with a
clear reason instead of calling the provider with an empty message list
(live 400: 'Input required: specify ...').
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import run_loop
from store import run_store as rs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "_DB_PATH", tmp_path / "runs.sqlite")
    monkeypatch.setattr(rs, "_vault_secret_values", lambda: frozenset())


def test_empty_journal_fails_fast_without_calling_the_model(monkeypatch):
    called = []
    monkeypatch.setattr(run_loop, "_execute_tool", lambda n, a: "unused")
    rid = rs.create_run("no input journaled")  # note: NO user_msg appended
    out = run_loop.run_rounds(rid, lambda m, t: called.append(m) or
                              {"text": "should not happen", "tool_calls": []},
                              tools=[])
    assert called == [], "the model must not be called with an empty message list"
    assert out == ""
    run = rs.get_run(rid)
    assert run["status"] == "failed" and run["resumable"] is True
    reasons = [e["payload"].get("reason") for e in rs.get_events(rid)
               if e["kind"] == "status"]
    assert any("no user message" in (r or "") for r in reasons), reasons


def test_system_only_journal_also_fails_fast():
    rid = rs.create_run("g")
    out = run_loop.run_rounds(rid, lambda m, t: pytest.fail("no call"),
                              tools=[], system="you are helpful")
    assert out == ""
    assert rs.get_run(rid)["status"] == "failed"


def test_normal_journal_with_user_msg_still_runs(monkeypatch):
    monkeypatch.setattr(run_loop, "_execute_tool", lambda n, a: "unused")
    rid = rs.create_run("g")
    rs.append_event(rid, "user_msg", {"text": "hello"})
    out = run_loop.run_rounds(rid, lambda m, t: {"text": "hi", "tool_calls": []},
                              tools=[])
    assert out == "hi" and rs.get_run(rid)["status"] == "succeeded"
