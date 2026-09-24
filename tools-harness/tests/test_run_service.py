"""Tests for agents/run_service.py — the M2 surface: start returns a run_id
immediately, progress streams from the journal, controls are journaled intents.

No threads or providers here: `start_run(execute=...)` takes the executor as a
dependency so the tests stay deterministic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import run_service as svc
from store import run_store as rs


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "_DB_PATH", tmp_path / "run_service.sqlite")
    monkeypatch.setattr(rs, "_vault_secret_values", lambda: frozenset())


def test_start_run_returns_id_and_journals_the_goal():
    captured = {}

    def execute(run_id):
        captured["run_id"] = run_id

    rid = svc.start_run("compare pricing", tools=["get_current_time"], execute=execute)
    assert rid and captured["run_id"] == rid
    run = rs.get_run(rid)
    assert run["goal"] == "compare pricing"
    assert run["trigger"] == "chat-send"
    kinds = [e["kind"] for e in rs.get_events(rid)]
    assert kinds == ["run", "user_msg"]
    assert rs.get_events(rid)[1]["payload"]["text"] == "compare pricing"


def test_stream_events_yields_in_seq_order_and_stops_at_terminal():
    rid = svc.start_run("g", tools=[], execute=lambda r: (rs.set_status(r, "running"),
                             rs.set_status(r, "succeeded")))
    events = list(svc.stream_events(rid, poll_s=0.01, max_wait_s=2))
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert rs.get_run(rid)["status"] == "succeeded"


def test_stream_events_resumes_from_after_seq():
    rid = svc.start_run("g", tools=[], execute=lambda r: (rs.set_status(r, "running"),
                             rs.set_status(r, "succeeded")))
    all_events = list(svc.stream_events(rid, poll_s=0.01, max_wait_s=2))
    tail = list(svc.stream_events(rid, after_seq=all_events[0]["seq"],
                                 poll_s=0.01, max_wait_s=2))
    assert [e["seq"] for e in tail] == [e["seq"] for e in all_events[1:]]


def test_controls_journal_intents_for_the_loop():
    rid = svc.start_run("g", tools=[], execute=lambda r: None)
    assert svc.pause(rid).startswith("Pausing")
    assert svc.kill(rid).startswith("Killing")
    assert svc.steer(rid, "focus on pricing").startswith("Steered")
    actions = [c["action"] for c in rs.pending_controls(rid)]
    assert actions == ["pause", "kill", "steer"]


def test_controls_rejected_on_terminal_run():
    rid = svc.start_run("g", tools=[], execute=lambda r: (rs.set_status(r, "running"),
                             rs.set_status(r, "succeeded")))
    out = svc.steer(rid, "too late")
    assert "already finished" in out
    assert rs.pending_controls(rid) == []


def test_unknown_run_reports_cleanly():
    assert "no run" in svc.pause("nope").lower()


def test_run_card_view_is_minimal_and_safe():
    rid = svc.start_run("secret hunt", tools=[], execute=lambda r: (rs.set_status(r, "running"),
                             rs.set_status(r, "succeeded")))
    card = svc.run_card(rid)
    assert card["run_id"] == rid
    assert card["status"] == "succeeded"
    assert "resumable" in card
    assert "password" not in str(card).lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
