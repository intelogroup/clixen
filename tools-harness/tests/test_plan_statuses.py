"""M4: plan statuses (beyond done) + journal linkage."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store import plan_store as ps
from store import run_store as rs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "_DB_PATH", tmp_path / "plan.sqlite")
    monkeypatch.setattr(rs, "_DB_PATH", tmp_path / "runs.sqlite")
    monkeypatch.setattr(rs, "_vault_secret_values", lambda: frozenset())


def test_plan_steps_carry_status_not_just_done():
    ps.set_plan("r1", ["gather", "analyse", "report"])
    ps.set_step_status("r1", 1, "in_progress")
    ps.set_step_status("r1", 2, "blocked")
    plan = ps.get_plan("r1")
    assert plan["statuses"] == ["pending", "in_progress", "blocked"]
    assert plan["done"] == [], "in_progress is not done"
    ps.set_step_status("r1", 1, "done")
    plan = ps.get_plan("r1")
    assert plan["done"] == [1] and plan["statuses"][1] == "done"


def test_unknown_status_rejected():
    ps.set_plan("r1", ["a"])
    with pytest.raises(ValueError, match="unknown step status"):
        ps.set_step_status("r1", 0, "wat")


def test_out_of_range_step_rejected():
    ps.set_plan("r1", ["a"])
    with pytest.raises(IndexError):
        ps.set_step_status("r1", 5, "done")


def test_mark_done_remains_compatible():
    ps.set_plan("r1", ["a", "b"])
    ps.mark_done("r1", [0])
    assert ps.get_plan("r1")["statuses"] == ["done", "pending"]


def test_plan_block_marks_non_pending_steps():
    ps.set_plan("r1", ["a", "b"])
    ps.set_step_status("r1", 0, "in_progress")
    ps.set_step_status("r1", 1, "blocked")
    block = ps.plan_block("r1")
    assert "[>] a" in block, block          # in_progress
    assert "[!] b" in block, block          # blocked
    assert "Blocked steps needing attention: b" in block, block


def test_journal_plan_events_rehydrate_plan():
    """Plan transitions are journaled, and a resume rebuilds plan state from
    the journal (not from plan_store alone)."""
    rid = rs.create_run("g")
    rs.append_event(rid, "plan_step", {"index": 0, "status": "done",
                                       "steps": ["a", "b"]})
    rs.append_event(rid, "plan_step", {"index": 1, "status": "in_progress",
                                       "steps": ["a", "b"]})
    plan = rs.plan_from_journal(rid)
    assert plan["steps"] == ["a", "b"]
    assert plan["statuses"] == ["done", "in_progress"]
    assert rs.plan_from_journal(rs.create_run("empty")) is None
