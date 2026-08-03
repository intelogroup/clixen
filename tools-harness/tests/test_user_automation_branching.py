"""
Tests for the generic conditional-branching primitive added to
jobs/handlers/user_automation.py's workflow step interpreter: llm_extract
step action, condition/on_pass/on_fail/goto jump routing, and
jobs/gate_rules.py's declarative rule evaluator.

Monkeypatches real module attributes (never sys.modules[...] = fake_module)
because tools/registry.py imports symbols from tools.imessage_search /
tools._registry.executors at load time — a wholesale module swap breaks
those unrelated imports. This gotcha was hit and fixed while wiring BCH's
calendar-event creation moments before this file was written.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.gcalendar as gcal_mod
from tools.registry import EXECUTORS
from jobs.handlers.user_automation import handle
from jobs import gate_rules


def _make_ollama_stub(payload: dict):
    class _FakeResp(dict):
        pass

    class _FakeClient:
        def chat(self, *args, **kwargs):
            return {"message": {"content": json.dumps(payload)}}

    class _FakeOllamaModule:
        Client = _FakeClient

    return _FakeOllamaModule


def _install_ollama_stub(monkeypatch, payload: dict):
    fake_mod = _make_ollama_stub(payload)
    monkeypatch.setitem(sys.modules, "ollama", fake_mod)


def test_backward_compat_linear_workflow_unchanged():
    instance = {
        "id": "test-instance",
        "action_type": "workflow",
        "task_name": "test_wf",
        "config": {
            "steps": [
                {"name": "step1", "action": "notification", "params": {"message": "first"}},
                {"name": "step2", "action": "notification", "params": {"message": "prev: $step1.result"}},
            ],
        },
    }
    result = handle(instance)
    assert result["success"] is True
    assert result["steps"]["step1"]["success"] is True
    assert result["steps"]["step2"]["success"] is True
    assert "gated" not in result["steps"]["step1"]
    assert "passed" not in result["steps"]["step1"]


def _branch_instance(condition):
    return {
        "id": "test-branch",
        "action_type": "workflow",
        "task_name": "test_branch",
        "config": {
            "steps": [
                {"name": "extract", "action": "llm_extract",
                 "params": {"prompt": "extract", "schema": {"type": "object"}}},
                {"name": "gate", "action": "notification", "params": {"message": "gating"},
                 "condition": condition, "on_pass": "accept", "on_fail": "decline"},
                {"name": "accept", "action": "notification", "params": {"message": "accepted"}, "goto": "__end__"},
                {"name": "decline", "action": "notification", "params": {"message": "declined"}},
            ],
        },
    }


def test_condition_on_pass_branch_fires(monkeypatch):
    _install_ollama_stub(monkeypatch, {"location": "Boston", "hour": 10})
    condition = {"all": [{"field": "extract.result.hour", "op": "gte", "value": 8}]}
    result = handle(_branch_instance(condition))
    assert result["steps"]["gate"]["passed"] is True
    assert "accept" in result["steps"]
    assert "decline" not in result["steps"]


def test_condition_on_fail_branch_fires(monkeypatch):
    _install_ollama_stub(monkeypatch, {"location": "Boston", "hour": 5})
    condition = {"all": [{"field": "extract.result.hour", "op": "gte", "value": 8}]}
    result = handle(_branch_instance(condition))
    assert result["steps"]["gate"]["gated"] is True
    assert "decline" in result["steps"]
    assert "accept" not in result["steps"]


def _calendar_branch_instance(extracted_when: str):
    return {
        "id": "test-cal-branch",
        "action_type": "workflow",
        "task_name": "test_cal_branch",
        "config": {
            "capability_mode": "execute",  # accept branch writes via create_calendar_event
            "steps": [
                {"name": "extract", "action": "llm_extract",
                 "params": {"prompt": "extract", "schema": {"type": "object"}}},
                {"name": "gate", "action": "notification", "params": {"message": "gating"},
                 "condition": {"all": [{"field": "extract.result.when", "op": "calendar_free"}]},
                 "on_pass": "accept", "on_fail": "decline"},
                {"name": "accept", "action": "tool_call",
                 "params": {"tool": "create_calendar_event", "title": "Test Assignment",
                            "start_iso": extracted_when}, "goto": "__end__"},
                {"name": "decline", "action": "notification", "params": {"message": "declined"}},
            ],
        },
    }


def test_calendar_free_gate_blocks_on_conflict(monkeypatch):
    _install_ollama_stub(monkeypatch, {"when": "2026-07-26T09:00:00-04:00"})
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60: "Existing Event at 09:00")

    calls = []
    monkeypatch.setitem(EXECUTORS, "create_calendar_event", lambda args: calls.append(args) or "should not be called")

    result = handle(_calendar_branch_instance("2026-07-26T09:00:00-04:00"))
    assert "decline" in result["steps"]
    assert "accept" not in result["steps"]
    assert calls == []


def test_calendar_free_gate_allows_and_creates_event(monkeypatch):
    _install_ollama_stub(monkeypatch, {"when": "2026-07-26T09:00:00-04:00"})
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60: None)

    calls = []

    def _fake_create(args):
        calls.append(args)
        return "Event created: 'Test Assignment' (ID: fake123)"

    monkeypatch.setitem(EXECUTORS, "create_calendar_event", _fake_create)

    result = handle(_calendar_branch_instance("2026-07-26T09:00:00-04:00"))
    assert "accept" in result["steps"]
    assert "decline" not in result["steps"]
    assert len(calls) == 1
    assert calls[0]["start_iso"] == "2026-07-26T09:00:00-04:00"


def test_membership_and_contains_ops():
    results = {"x": {"result": {"loc": "Boston", "hour": 10}}}
    assert gate_rules.eval_rule({"field": "x.result.loc", "op": "eq", "value": "Boston"}, results) is True
    assert gate_rules.eval_rule({"field": "x.result.loc", "op": "neq", "value": "Boston"}, results) is False
    assert gate_rules.eval_rule({"field": "x.result.loc", "op": "in", "value": ["Boston", "Newton"]}, results) is True
    assert gate_rules.eval_rule({"field": "x.result.loc", "op": "not_in", "value": ["Burlington"]}, results) is True
    assert gate_rules.eval_rule({"field": "x.result.hour", "op": "gte", "value": 10}, results) is True
    assert gate_rules.eval_rule({"field": "x.result.hour", "op": "lte", "value": 10}, results) is True
    assert gate_rules.eval_rule({"field": "x.result.hour", "op": "gt", "value": 10}, results) is False
    assert gate_rules.eval_rule({"field": "x.result.hour", "op": "lt", "value": 10}, results) is False
    assert gate_rules.eval_rule({"field": "x.result.loc", "op": "contains", "value": "bos"}, results) is True
    assert gate_rules.eval_condition(
        {"any": [{"field": "x.result.hour", "op": "lt", "value": 5},
                 {"field": "x.result.loc", "op": "eq", "value": "Boston"}]}, results
    ) is True


def test_unknown_op_fails_closed():
    assert gate_rules.eval_rule({"field": "x.result.hour", "op": "made_up_op", "value": 1}, {}) is False


def test_weekday_ops():
    results = {"x": {"when": "2026-07-25T14:00:00-04:00"}}  # Saturday
    assert gate_rules.eval_rule({"field": "x.when", "op": "is_weekend"}, results) is True
    assert gate_rules.eval_rule({"field": "x.when", "op": "weekday_in", "value": ["Saturday", "Sunday"]}, results) is True
    assert gate_rules.eval_rule({"field": "x.when", "op": "weekday_not_in", "value": ["Saturday", "Sunday"]}, results) is False
    weekday_results = {"x": {"when": "2026-07-27T14:00:00-04:00"}}  # Monday
    assert gate_rules.eval_rule({"field": "x.when", "op": "is_weekend"}, weekday_results) is False
    assert gate_rules.eval_rule({"field": "x.when", "op": "weekday_not_in", "value": ["Saturday", "Sunday"]}, weekday_results) is True


def test_bad_goto_target_does_not_crash():
    instance = {
        "id": "test-bad-goto",
        "action_type": "workflow",
        "task_name": "test_bad_goto",
        "config": {
            "steps": [
                {"name": "step1", "action": "notification", "params": {"message": "hi"}, "goto": "nonexistent"},
            ],
        },
    }
    result = handle(instance)
    assert isinstance(result, dict)
    assert "bad goto target" in result["steps"]["step1"]["error"]


if __name__ == "__main__":
    test_backward_compat_linear_workflow_unchanged()
    test_membership_and_contains_ops()
    test_unknown_op_fails_closed()
    test_bad_goto_target_does_not_crash()
    print("user_automation branching tests (non-monkeypatch subset) passed — run via pytest for full coverage")
