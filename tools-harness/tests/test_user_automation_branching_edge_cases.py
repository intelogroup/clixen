"""
Hard/adversarial edge cases for the branching workflow engine
(jobs/handlers/user_automation.py + jobs/gate_rules.py), on top of the
baseline coverage in tests/test_user_automation_branching.py:

- infinite goto loop actually terminates (MAX_STEPS guard)
- multi-gate (3-deep) chained branching, short-circuiting on first failure
- exception-during-a-gated-step precedence (goto wins over on_pass/on_fail
  when the step's own action raises, not just when the condition fails)
- condition present but no on_pass/on_fail configured (silent fallthrough —
  a real footgun: an LLM author who forgets the jump target gets linear
  execution regardless of pass/fail, not a loud error)
- malformed calendar_free input (non-ISO string)
- nested boolean conditions ({'any': [{'all': [...]}]}) are NOT supported —
  pins the current flat-only limitation instead of leaving it undocumented
- $stepname.field text interpolation still works on a step that also has a
  structural 'condition' evaluated against the same results dict
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.gcalendar as gcal_mod
import store.workflow_store as workflow_store
from tools.registry import EXECUTORS
from jobs.handlers.user_automation import handle
from jobs import gate_rules


def _ollama_stub(payload: dict):
    class _FakeClient:
        def chat(self, *a, **kw):
            return {"message": {"content": json.dumps(payload)}}

    class _FakeOllamaModule:
        Client = _FakeClient

    return _FakeOllamaModule


def _install_ollama(monkeypatch, payload):
    monkeypatch.setitem(sys.modules, "ollama", _ollama_stub(payload))


@pytest.mark.timeout(5)
def test_infinite_goto_loop_terminates_via_max_steps():
    instance = {
        "id": "test-loop",
        "action_type": "workflow",
        "task_name": "test_loop",
        "config": {
            "steps": [
                {"name": "a", "action": "notification", "params": {"message": "a"}, "goto": "b"},
                {"name": "b", "action": "notification", "params": {"message": "b"}, "goto": "a"},
            ],
        },
    }
    # Must return (not hang) within the pytest.mark.timeout window — the
    # MAX_STEPS = len(steps)*4 guard is what makes this terminate at all.
    result = handle(instance)
    assert isinstance(result, dict)
    assert set(result["steps"].keys()) == {"a", "b"}


def _three_gate_instance(extract_payload, gate2_threshold=8):
    return {
        "id": "test-three-gate",
        "action_type": "workflow",
        "task_name": "test_three_gate",
        "config": {
            "steps": [
                {"name": "extract", "action": "llm_extract",
                 "params": {"prompt": "extract", "schema": {"type": "object"}}},
                {"name": "gate1_location", "action": "notification", "params": {"message": "check location"},
                 "condition": {"all": [{"field": "extract.result.location", "op": "not_in", "value": ["Burlington"]}]},
                 "on_pass": "gate2_hour", "on_fail": "decline"},
                {"name": "gate2_hour", "action": "notification", "params": {"message": "check hour"},
                 "condition": {"all": [{"field": "extract.result.hour", "op": "gte", "value": gate2_threshold}]},
                 "on_pass": "gate3_calendar", "on_fail": "decline"},
                {"name": "gate3_calendar", "action": "notification", "params": {"message": "check calendar"},
                 "condition": {"all": [{"field": "extract.result.when", "op": "calendar_free"}]},
                 "on_pass": "accept", "on_fail": "decline"},
                {"name": "accept", "action": "notification", "params": {"message": "ACCEPTED"}, "goto": "__end__"},
                {"name": "decline", "action": "notification", "params": {"message": "DECLINED"}},
            ],
        },
    }


def test_three_gate_chain_all_pass_reaches_accept(monkeypatch):
    _install_ollama(monkeypatch, {"location": "Boston", "hour": 10, "when": "2026-08-01T09:00:00-04:00"})
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60: None)

    result = handle(_three_gate_instance({}))
    steps = result["steps"]
    assert steps["gate1_location"]["passed"] is True
    assert steps["gate2_hour"]["passed"] is True
    assert steps["gate3_calendar"]["passed"] is True
    assert "accept" in steps
    assert "decline" not in steps


def test_three_gate_chain_short_circuits_on_first_failure(monkeypatch):
    # location blacklisted -> gate1 fails -> should jump straight to decline,
    # gate2/gate3 must NEVER be evaluated (proves short-circuit, not just
    # "eventually declines").
    _install_ollama(monkeypatch, {"location": "Burlington", "hour": 10, "when": "2026-08-01T09:00:00-04:00"})
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60: None)

    result = handle(_three_gate_instance({}))
    steps = result["steps"]
    assert steps["gate1_location"]["gated"] is True
    assert "gate2_hour" not in steps
    assert "gate3_calendar" not in steps
    assert "decline" in steps
    assert "accept" not in steps


def test_three_gate_chain_fails_at_middle_gate(monkeypatch):
    # location ok, hour too low -> gate2 fails -> decline. gate3 (calendar)
    # must never run.
    _install_ollama(monkeypatch, {"location": "Boston", "hour": 5, "when": "2026-08-01T09:00:00-04:00"})
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60: None)

    result = handle(_three_gate_instance({}, gate2_threshold=8))
    steps = result["steps"]
    assert steps["gate1_location"]["passed"] is True
    assert steps["gate2_hour"]["gated"] is True
    assert "gate3_calendar" not in steps
    assert "decline" in steps
    assert "accept" not in steps


@pytest.mark.timeout(15)
def test_exception_on_gated_step_ignores_on_pass_uses_goto_or_falls_through(monkeypatch):
    # A step has BOTH a passing condition AND an action that raises a real
    # Python exception at the _exec_step level. NOTE: 'tool_call' can't be
    # used for this — tools/registry.py:execute_tool() already wraps every
    # executor in its own try/except and returns a "[tool error] ..."
    # STRING on failure, never raises (confirmed by an earlier version of
    # this test that used tool_call and failed: the step came back
    # success=True with an error-string result, not an exception). Real
    # exceptions only reach _exec_step's except clause via the handful of
    # step actions that call things directly — http_webhook's
    # requests.post() is one; a connection refused to a closed local port
    # raises ConnectionError before any HTTP response exists.
    #
    # This IS a real, worth-flagging engine finding: a 'tool_call' step
    # failing never triggers failure_mode/on_pass/on_fail-via-exception at
    # all, regardless of failure_mode. Only http_webhook/email/imessage/
    # approval steps (which call things directly, unwrapped) can actually
    # raise and hit this precedence rule.
    instance = {
        "id": "test-exc-precedence",
        "action_type": "workflow",
        "task_name": "test_exc_precedence",
        "config": {
            "steps": [
                {"name": "gate", "action": "http_webhook",
                 "params": {"url": "http://127.0.0.1:1/unreachable"},
                 "condition": {"all": [{"field": "x", "op": "eq", "value": None}]},  # unresolvable 'x' -> None == None -> PASSES
                 "on_pass": "should_never_run", "on_fail": "should_never_run_either",
                 "failure_mode": "continue"},
                {"name": "fallthrough", "action": "notification", "params": {"message": "reached via cur+=1"},
                 "goto": "__end__"},
                {"name": "should_never_run", "action": "notification", "params": {"message": "must not run"}},
                {"name": "should_never_run_either", "action": "notification", "params": {"message": "must not run"}},
            ],
        },
    }
    result = handle(instance)
    steps = result["steps"]
    assert steps["gate"]["success"] is False
    assert "should_never_run" not in steps
    assert "should_never_run_either" not in steps
    assert "fallthrough" in steps, "expected cur+=1 fallthrough, not on_pass, after an exception on a gated step"


def test_tool_call_step_failure_now_engages_failure_mode(monkeypatch):
    # Was a real bug (see git history / CLAUDE.md 2026-07-16): execute_tool()
    # never raises, so a failing tool_call step used to come back
    # success=True with an error STRING as its "result", silently defeating
    # failure_mode='stop'/'rollback'. Fixed: the tool_call branch in
    # _exec_step now re-raises when is_error_result() is true, so normal
    # failure_mode semantics apply — this test pins the FIXED behavior.
    instance = {
        "id": "test-tool-call-now-raises",
        "action_type": "workflow",
        "task_name": "test_tool_call_now_raises",
        "config": {
            "steps": [
                {"name": "bad_tool", "action": "tool_call",
                 "params": {"tool": "this_tool_does_not_exist"}, "failure_mode": "stop"},
                {"name": "after", "action": "notification", "params": {"message": "should not run"}},
            ],
        },
    }
    result = handle(instance)
    steps = result["steps"]
    assert steps["bad_tool"]["success"] is False
    assert "Unknown tool" in steps["bad_tool"]["error"]
    assert "after" not in steps, "failure_mode='stop' must halt the workflow now that tool_call raises on failure"


def test_tool_call_failure_gateable_via_is_error_condition(monkeypatch):
    # The complementary fix: a downstream (or the same) step's condition can
    # gate on whether an earlier tool_call actually succeeded via the new
    # 'is_error' op, without needing the exception path at all.
    def _bad_tool(args):
        return "[tool error] simulated_tool failed: boom"

    monkeypatch.setitem(EXECUTORS, "simulated_tool", _bad_tool)

    instance = {
        "id": "test-is-error-condition",
        "action_type": "workflow",
        "task_name": "test_is_error_condition",
        "config": {
            "steps": [
                # failure_mode 'continue' (not stop/rollback) so this step's
                # own raise-on-error doesn't halt the run — we want to reach
                # the downstream gate and prove IT can see the failure too.
                {"name": "call_tool", "action": "tool_call",
                 "params": {"tool": "simulated_tool"}, "failure_mode": "continue"},
                {"name": "gate", "action": "notification", "params": {"message": "gate"},
                 "condition": {"all": [{"field": "call_tool.error", "op": "is_error"}]},
                 "on_pass": "handle_failure", "on_fail": "handle_success"},
                {"name": "handle_failure", "action": "notification",
                 "params": {"message": "tool failed, alerting"}, "goto": "__end__"},
                {"name": "handle_success", "action": "notification", "params": {"message": "tool ok"}},
            ],
        },
    }
    result = handle(instance)
    steps = result["steps"]
    assert steps["call_tool"]["success"] is False
    assert "handle_failure" in steps
    assert "handle_success" not in steps


def test_condition_without_jump_targets_falls_through_linearly(monkeypatch):
    # Footgun check: a 'condition' with no on_pass/on_fail configured just
    # proceeds to the next step in list order regardless of pass/fail — it
    # does NOT stop the workflow or raise. This is current behavior, pinned
    # here so a future change to "require jump targets when condition is
    # present" is a deliberate decision, not an accidental regression.
    instance = {
        "id": "test-no-jump-targets",
        "action_type": "workflow",
        "task_name": "test_no_jump_targets",
        "config": {
            "steps": [
                {"name": "gate", "action": "notification", "params": {"message": "gate"},
                 "condition": {"all": [{"field": "nothing.here", "op": "eq", "value": "x"}]}},  # always fails
                {"name": "next", "action": "notification", "params": {"message": "still runs"}},
            ],
        },
    }
    result = handle(instance)
    steps = result["steps"]
    assert steps["gate"]["gated"] is True
    assert "next" in steps, "step after a gate with no on_fail must still run (cur+=1 fallthrough)"


def test_forgotten_terminal_goto_falls_through_to_next_declared_step(monkeypatch):
    # Another footgun: an 'accept' step meant to be terminal but missing
    # goto:'__end__' does NOT stop the workflow — it just runs whatever step
    # is declared next in the list. Pinning this so it's documented behavior,
    # not a silent surprise.
    instance = {
        "id": "test-forgotten-goto",
        "action_type": "workflow",
        "task_name": "test_forgotten_goto",
        "config": {
            "steps": [
                {"name": "accept", "action": "notification", "params": {"message": "accepted"}},  # no goto!
                {"name": "unexpected_extra_step", "action": "notification", "params": {"message": "ran anyway"}},
            ],
        },
    }
    result = handle(instance)
    steps = result["steps"]
    assert "accept" in steps
    assert "unexpected_extra_step" in steps, "missing terminal goto must fall through, not stop"


def test_calendar_free_with_malformed_datetime_fails_closed(monkeypatch):
    assert gate_rules.eval_rule(
        {"field": "extract.result.when", "op": "calendar_free"},
        {"extract": {"result": {"when": "not-a-real-date"}}},
    ) is False
    assert gate_rules.eval_rule(
        {"field": "extract.result.when", "op": "calendar_free"},
        {"extract": {"result": {"when": None}}},
    ) is False
    assert gate_rules.eval_rule(
        {"field": "extract.result.missing", "op": "calendar_free"},
        {"extract": {"result": {}}},
    ) is False


def test_nested_boolean_conditions_supported():
    # eval_rule recurses into a nested {'all'/'any': [...]} block (gate_rules
    # line 44) — lets an LLM-authored condition express "(A and B) or (C and D)"
    # as one condition instead of needing an extra workflow step. Pinned here so
    # a future refactor doesn't silently break AND-of-ORs.
    nested = {"any": [
        {"all": [{"field": "x", "op": "eq", "value": 1}]},
        {"field": "y", "op": "eq", "value": 2},
    ]}
    results = {"x": 1, "y": 2}
    # 'any' short-circuits on the flat branch even if the nested branch is False...
    assert gate_rules.eval_condition(nested, results) is True
    # ...and the nested branch itself evaluates correctly (NOT inert).
    assert gate_rules.eval_rule(nested["any"][0], results) is True
    # Fail-closed when no branch matches.
    assert gate_rules.eval_condition({"any": [
        {"all": [{"field": "x", "op": "eq", "value": 99}]},
    ]}, results) is False


def test_unknown_op_logs_a_distinct_warning(caplog):
    # A genuinely unknown op still fails closed (never raises — eval_condition
    # is called unguarded in the engine) but logs a distinct warning so an
    # operator can tell "typo'd op name" apart from any other failure. Nested
    # boolean blocks are a supported feature (see the test above) and must NOT
    # log the unknown-op warning.
    with caplog.at_level("WARNING", logger="jobs.gate_rules"):
        gate_rules.eval_rule({"field": "x", "op": "made_up_op", "value": 1}, {"x": 1})
    assert any("unknown condition op" in r.message for r in caplog.records)
    assert not any("nested boolean condition" in r.message for r in caplog.records)

    caplog.clear()
    with caplog.at_level("WARNING", logger="jobs.gate_rules"):
        gate_rules.eval_rule({"all": [{"field": "x", "op": "eq", "value": 1}]}, {"x": 1})
    assert not any("unknown condition op" in r.message for r in caplog.records)


def test_interpolation_and_condition_coexist_on_the_same_results_dict(monkeypatch):
    # A later step should be able to BOTH text-interpolate an extracted
    # field into its params (for a human-readable message) AND have that
    # same extraction gate an earlier condition — proving the two
    # mechanisms (string _interp vs structural eval_condition) read from
    # the same 'results' accumulator consistently.
    _install_ollama(monkeypatch, {"location": "Cambridge", "hour": 9})
    captured = []
    monkeypatch.setattr(
        workflow_store, "add_notification",
        lambda message, source="automation", level="info": captured.append(message),
    )

    instance = {
        "id": "test-interp-and-condition",
        "action_type": "workflow",
        "task_name": "test_interp_and_condition",
        "config": {
            "steps": [
                {"name": "extract", "action": "llm_extract",
                 "params": {"prompt": "extract", "schema": {"type": "object"}}},
                {"name": "gate", "action": "notification", "params": {"message": "gate"},
                 "condition": {"all": [{"field": "extract.result.hour", "op": "gte", "value": 8}]},
                 "on_pass": "announce", "on_fail": "decline"},
                {"name": "announce", "action": "notification",
                 "params": {"message": "Accepted for $extract.result.location at hour $extract.result.hour"},
                 "goto": "__end__"},
                {"name": "decline", "action": "notification", "params": {"message": "declined"}},
            ],
        },
    }
    result = handle(instance)
    assert "announce" in result["steps"]
    assert "decline" not in result["steps"]
    assert any("Cambridge" in m and "9" in m for m in captured), captured


def test_condition_only_step_with_no_action_is_a_clean_noop():
    # Found live: an LLM author building a "pure gate" step naturally omits
    # 'action' (there's nothing to DO, just something to check). Used to
    # leave a cosmetic "[error] unknown step action: None" in the gate
    # step's own result (harmless — branching worked — but confusing log
    # noise). Fixed 2026-07-16: action=None is now an explicit no-op.
    instance = {
        "id": "test-no-action-gate",
        "action_type": "workflow",
        "task_name": "test_no_action_gate",
        "config": {
            "steps": [
                {"name": "gate", "condition": {"all": [{"field": "x", "op": "eq", "value": None}]},
                 "on_pass": "accept", "on_fail": "decline"},
                {"name": "accept", "action": "notification", "params": {"message": "ok"}, "goto": "__end__"},
                {"name": "decline", "action": "notification", "params": {"message": "no"}},
            ],
        },
    }
    result = handle(instance)
    steps = result["steps"]
    assert steps["gate"]["success"] is True
    assert steps["gate"]["passed"] is True
    assert "unknown step action" not in steps["gate"]["result"]
    assert "accept" in steps
    assert "decline" not in steps


if __name__ == "__main__":
    test_infinite_goto_loop_terminates_via_max_steps()
    test_condition_without_jump_targets_falls_through_linearly(None)
    test_forgotten_terminal_goto_falls_through_to_next_declared_step(None)
    test_calendar_free_with_malformed_datetime_fails_closed(None)
    test_nested_boolean_conditions_supported()
    test_tool_call_step_failure_now_engages_failure_mode(None)
    print("branching edge-case tests (non-monkeypatch subset) passed — run via pytest for full coverage")
