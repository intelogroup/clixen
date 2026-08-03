"""Unit tests for store/plan_store.py — sqlite-backed mid-task plan state."""
import uuid

from store import plan_store


def _rid():
    return "test-" + uuid.uuid4().hex[:8]


def test_set_and_get_plan():
    rid = _rid()
    plan_store.set_plan(rid, ["step a", "step b", "step c"])
    plan = plan_store.get_plan(rid)
    assert plan == {"steps": ["step a", "step b", "step c"], "done": []}


def test_mark_done_updates_state():
    rid = _rid()
    plan_store.set_plan(rid, ["a", "b", "c"])
    result = plan_store.mark_done(rid, [0, 2])
    assert result == {"steps": ["a", "b", "c"], "done": [0, 2]}
    assert plan_store.get_plan(rid)["done"] == [0, 2]


def test_mark_done_ignores_out_of_range():
    rid = _rid()
    plan_store.set_plan(rid, ["a"])
    result = plan_store.mark_done(rid, [0, 5, -1])
    assert result["done"] == [0]


def test_mark_done_no_plan_returns_none():
    assert plan_store.mark_done(_rid(), [0]) is None


def test_set_plan_replaces_and_resets_done():
    rid = _rid()
    plan_store.set_plan(rid, ["a", "b"])
    plan_store.mark_done(rid, [0])
    plan_store.set_plan(rid, ["x", "y", "z"])
    assert plan_store.get_plan(rid) == {"steps": ["x", "y", "z"], "done": []}


def test_plan_block_empty_when_no_plan():
    assert plan_store.plan_block(_rid()) == ""


def test_plan_block_renders_checkboxes():
    rid = _rid()
    plan_store.set_plan(rid, ["a", "b"])
    plan_store.mark_done(rid, [1])
    block = plan_store.plan_block(rid)
    assert "[ ] a" in block
    assert "[x] b" in block
