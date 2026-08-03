"""
Regression guard for the plan-mode write-lockout bug: PLAN_MODE_ACTIVE was set True by
the plan intent branch and only reset on the url_fetch fast-path (unreachable for plan
requests), so one plan request left write tools permanently blocked process-wide until
restart. harness.run() now resets the flag in its finally on every exit path.

Hermetic — no model/network calls: _run_impl is stubbed so only the run() wrapper's
try/finally semantics are exercised.
"""

import harness
from tools import registry as _reg


class _Boom(Exception):
    pass


def test_plan_mode_flag_resets_after_normal_exit(monkeypatch):
    _reg.PLAN_MODE_ACTIVE = True  # simulate a plan run that set the flag
    monkeypatch.setattr(harness, "_run_impl", lambda *a, **k: "ok")

    harness.run("hello")

    assert _reg.PLAN_MODE_ACTIVE is False, "flag must reset after run() returns"


def test_plan_mode_flag_resets_after_raise(monkeypatch):
    _reg.PLAN_MODE_ACTIVE = True

    def _boom(*a, **k):
        raise _Boom("simulated failure")

    monkeypatch.setattr(harness, "_run_impl", _boom)

    try:
        harness.run("hello")
    except _Boom:
        pass
    else:
        raise AssertionError("expected _Boom to propagate")

    assert _reg.PLAN_MODE_ACTIVE is False, "flag must reset even when run() raises"


def test_execute_tool_blocks_write_tools_in_plan_mode_and_allows_after(monkeypatch):
    _reg.PLAN_MODE_ACTIVE = True
    blocked = _reg.execute_tool("write_file", {"path": "/tmp/x", "content": "x"})
    assert blocked.startswith("[blocked]"), "write_file must be blocked while flag is set"

    _reg.PLAN_MODE_ACTIVE = False
    # With the flag clear, write_file falls through to the real executor — patch it so the
    # test stays hermetic instead of actually writing to disk.
    monkeypatch.setattr(_reg, "EXECUTORS", {**_reg.EXECUTORS, "write_file": lambda a: "written"})
    allowed = _reg.execute_tool("write_file", {"path": "/tmp/x", "content": "x"})
    assert allowed == "written", "write_file must be executable once the flag clears"
