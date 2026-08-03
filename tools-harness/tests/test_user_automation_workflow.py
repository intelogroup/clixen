"""
Regression tests for jobs/handlers/user_automation.py's workflow step
interpreter. The 'workflow' action_type existed and reached real code, but
cross-step templating was silently broken: str.format_map with dotted keys
like "step.notify" either raised AttributeError (masked by a bare except,
template left unsubstituted) or, for the double-brace form the code's own
smoke test used ("{{ step.notify.result }}"), parsed as escaped literal
braces and never substituted at all. Fixed with regex-based $stepname.field
substitution — these tests pin that behavior down.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jobs.handlers.user_automation import _interp, handle


def test_interp_substitutes_step_result():
    results = {"notify": {"success": True, "result": "ok"}}
    assert _interp("prev step said: $notify.result", results) == "prev step said: ok"


def test_interp_substitutes_step_error():
    results = {"check": {"success": False, "error": "boom"}}
    assert _interp("failed because: $check.error", results) == "failed because: boom"


def test_interp_missing_step_ref_yields_empty_string():
    assert _interp("value: $missing.result", {}) == "value: "


def test_interp_recurses_into_dict_and_list():
    results = {"a": {"success": True, "result": "X"}}
    obj = {"message": "got $a.result", "items": ["prefix-$a.result"]}
    out = _interp(obj, results)
    assert out == {"message": "got X", "items": ["prefix-X"]}


def test_interp_does_not_touch_curly_brace_syntax():
    # The old broken forms must not accidentally "work" some other way —
    # only $stepname.field substitutes.
    results = {"notify": {"success": True, "result": "ok"}}
    assert _interp("{notify.result}", results) == "{notify.result}"
    assert _interp("{{ notify.result }}", results) == "{{ notify.result }}"


def test_workflow_handle_end_to_end_cross_step_substitution():
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


if __name__ == "__main__":
    test_interp_substitutes_step_result()
    test_interp_substitutes_step_error()
    test_interp_missing_step_ref_yields_empty_string()
    test_interp_recurses_into_dict_and_list()
    test_interp_does_not_touch_curly_brace_syntax()
    test_workflow_handle_end_to_end_cross_step_substitution()
    print("user_automation workflow templating tests passed")
