"""Declarative field/op/value rule evaluator for workflow step conditions.

Config here is authored by an LLM via create_automation, not a developer —
no eval()/arbitrary code, ever. Unknown op or unresolvable field fails
closed (returns False) instead of raising, so a malformed condition just
routes to on_fail rather than crashing the workflow run.
"""
from __future__ import annotations

import logging
from datetime import datetime

_log = logging.getLogger(__name__)


def _resolve(field: str, results: dict):
    """Dotted-path lookup into the accumulated step results dict, e.g.
    'extract.result.location' -> results['extract']['result']['location'].
    Returns None if any segment is missing or not a dict."""
    cur = results
    for part in field.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


_OPS = {
    "eq": lambda v, target: v == target,
    "neq": lambda v, target: v != target,
    "in": lambda v, target: v in (target or []),
    "not_in": lambda v, target: v not in (target or []),
    "gte": lambda v, target: v is not None and v >= target,
    "lte": lambda v, target: v is not None and v <= target,
    "gt": lambda v, target: v is not None and v > target,
    "lt": lambda v, target: v is not None and v < target,
    "contains": lambda v, target: isinstance(v, str) and str(target).lower() in v.lower(),
}


def eval_rule(rule: dict, results: dict) -> bool:
    # A rule dict can itself be a nested {'all'/'any': [...]} block — lets an
    # LLM-authored condition express "(A and B) or (C and D)" as one condition
    # instead of needing an extra workflow step just to combine two gates.
    if "op" not in rule and ("all" in rule or "any" in rule):
        return eval_condition(rule, results)

    op = rule.get("op")
    field = rule.get("field", "")
    v = _resolve(field, results)

    if op == "calendar_free":
        from tools.gcalendar import check_calendar_conflict
        if not v:
            return False  # fail-safe: unresolvable input = not free
        try:
            at = datetime.fromisoformat(v)
        except (TypeError, ValueError):
            return False
        conflict = check_calendar_conflict(at, buffer_minutes=rule.get("buffer_minutes", 60))
        return conflict is None

    if op in ("is_weekend", "weekday_in", "weekday_not_in"):
        # Deterministic date-math ops — found live 2026-07-16: without these,
        # a weekend/weekday check cost an extra llm_extract round-trip just
        # to ask an LLM "what day of the week is this date?", which is both
        # slower and less reliable than Python's own stdlib for something
        # this mechanical. All three parse the field as an ISO datetime
        # string; malformed/missing input fails closed (not a weekend / not
        # matching), same convention as calendar_free.
        if not v:
            return False
        try:
            at = datetime.fromisoformat(v)
        except (TypeError, ValueError):
            return False
        weekday_name = at.strftime("%A")  # e.g. "Saturday"
        if op == "is_weekend":
            return weekday_name in ("Saturday", "Sunday")
        target = rule.get("value") or []
        target_lower = {str(d).lower() for d in target}
        if op == "weekday_in":
            return weekday_name.lower() in target_lower
        return weekday_name.lower() not in target_lower  # weekday_not_in

    if op == "is_error":
        # Lets a downstream condition gate on whether an earlier step's
        # result (e.g. a tool_call's output string) was actually an error,
        # using the same prefix-anchored detection every other error check
        # in this codebase uses — instead of an LLM-authored condition
        # having to string-match a literal "[tool error]"/"[blocked]" prefix
        # itself, which is fragile and easy to get subtly wrong.
        from tools.registry import is_error_result
        return isinstance(v, str) and is_error_result(v)

    fn = _OPS.get(op)
    if fn is None:
        # ponytail: fail closed on unknown/malformed op — no error surfaced
        # to the step result in this pass, just routes to on_fail. Logged
        # (not silent) so a typo'd op name is visible in server logs even
        # though the workflow run itself doesn't crash.
        _log.warning("gate_rules: unknown condition op %r in rule %r", op, rule)
        return False
    try:
        return bool(fn(v, rule.get("value")))
    except TypeError:
        return False


def eval_condition(condition: dict, results: dict) -> bool:
    """condition is {'all': [rule,...]}, {'any': [rule,...]}, or a bare rule dict."""
    if "all" in condition:
        return all(eval_rule(r, results) for r in condition["all"])
    if "any" in condition:
        return any(eval_rule(r, results) for r in condition["any"])
    return eval_rule(condition, results)


if __name__ == "__main__":
    results = {"extract": {"result": {"location": "Boston", "hour": 10}}}
    assert eval_rule({"field": "extract.result.hour", "op": "gte", "value": 8}, results) is True
    assert eval_rule({"field": "extract.result.hour", "op": "lt", "value": 8}, results) is False
    assert eval_rule({"field": "extract.result.location", "op": "not_in", "value": ["Burlington"]}, results) is True
    assert eval_rule({"field": "extract.result.location", "op": "in", "value": ["Burlington"]}, results) is False
    assert eval_rule({"field": "extract.result.location", "op": "contains", "value": "bos"}, results) is True
    assert eval_rule({"field": "missing.field", "op": "eq", "value": 1}, results) is False
    assert eval_rule({"field": "extract.result.hour", "op": "made_up", "value": 1}, results) is False
    assert eval_rule({"field": "bad_tool.result", "op": "is_error"},
                      {"bad_tool": {"result": "[tool error] xyz failed: boom"}}) is True
    assert eval_rule({"field": "good_tool.result", "op": "is_error"},
                      {"good_tool": {"result": "all good"}}) is False
    assert eval_rule({"field": "extract.when", "op": "is_weekend"},
                      {"extract": {"when": "2026-07-25T14:00:00-04:00"}}) is True  # Saturday
    assert eval_rule({"field": "extract.when", "op": "is_weekend"},
                      {"extract": {"when": "2026-07-27T14:00:00-04:00"}}) is False  # Monday
    assert eval_rule({"field": "extract.when", "op": "weekday_in", "value": ["Monday", "Wednesday"]},
                      {"extract": {"when": "2026-07-27T14:00:00-04:00"}}) is True  # Monday
    assert eval_rule({"field": "extract.when", "op": "weekday_not_in", "value": ["Monday", "Wednesday"]},
                      {"extract": {"when": "2026-07-27T14:00:00-04:00"}}) is False  # Monday, excluded
    assert eval_rule({"field": "extract.when", "op": "is_weekend"},
                      {"extract": {"when": "not-a-date"}}) is False
    assert eval_condition({"all": [
        {"field": "extract.result.hour", "op": "gte", "value": 8},
        {"field": "extract.result.location", "op": "not_in", "value": ["Burlington"]},
    ]}, results) is True
    assert eval_condition({"any": [
        {"field": "extract.result.hour", "op": "lt", "value": 8},
        {"field": "extract.result.location", "op": "not_in", "value": ["Burlington"]},
    ]}, results) is True
    # nested all-inside-any / any-inside-all
    assert eval_condition({"any": [
        {"all": [
            {"field": "extract.result.hour", "op": "gte", "value": 8},
            {"field": "extract.result.location", "op": "eq", "value": "Boston"},
        ]},
        {"field": "extract.result.hour", "op": "lt", "value": 0},
    ]}, results) is True
    assert eval_condition({"all": [
        {"any": [
            {"field": "extract.result.hour", "op": "lt", "value": 0},
            {"field": "extract.result.location", "op": "eq", "value": "Burlington"},
        ]},
        {"field": "extract.result.hour", "op": "gte", "value": 8},
    ]}, results) is False
    print("gate_rules smoke test assertions ok")
