"""_run_subagent: machine-readable footer on orchestrator subagent results."""
import re

import pytest

from tools import orchestrator_tools as ot


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Stub the intent pipeline and trace store; each test sets .trace."""
    state = {"trace": [], "captured_run_id": None}

    def _pipeline(intent, query, chat_id=None, run_id=None):
        state["captured_run_id"] = run_id
        return "subagent prose answer"

    import harness
    from store import trace_store
    monkeypatch.setattr(harness, "_execute_intent_pipeline", _pipeline)
    monkeypatch.setattr(trace_store, "get_trace", lambda rid: state["trace"])
    return state


def _footer(result: str) -> str:
    m = re.search(r"\[subagent [^\]]+\]$", result)
    assert m, f"no footer in: {result!r}"
    return m.group(0)


def test_ok_status_when_tools_succeeded(fake_pipeline):
    fake_pipeline["trace"] = [
        {"tool": "list_emails", "error": False},
        {"tool": "read_email", "error": False},
    ]
    out = ot._run_subagent("email", "check inbox")
    f = _footer(out)
    assert "intent=email" in f and "status=ok" in f
    assert "tools=list_emails,read_email" in f and "errors=0" in f
    assert out.startswith("subagent prose answer")


def test_degraded_when_zero_tools_called(fake_pipeline):
    fake_pipeline["trace"] = []
    f = _footer(ot._run_subagent("calendar", "events today"))
    assert "status=degraded" in f and "tools=none" in f


def test_degraded_when_all_tools_errored(fake_pipeline):
    fake_pipeline["trace"] = [
        {"tool": "list_emails", "error": True},
        {"tool": "list_emails", "error": True},
    ]
    f = _footer(ot._run_subagent("email", "check inbox"))
    assert "status=degraded" in f and "errors=2" in f


def test_ok_when_some_tools_errored(fake_pipeline):
    fake_pipeline["trace"] = [
        {"tool": "list_emails", "error": True},
        {"tool": "read_email", "error": False},
    ]
    f = _footer(ot._run_subagent("email", "check inbox"))
    assert "status=ok" in f and "errors=1" in f


def test_run_id_threaded_to_pipeline(fake_pipeline):
    ot._run_subagent("tasks", "list tasks")
    assert fake_pipeline["captured_run_id"], "run_id not passed to pipeline"


def test_footer_does_not_trip_error_detection(fake_pipeline):
    from tools.registry import is_error_result
    fake_pipeline["trace"] = [{"tool": "list_emails", "error": True}]
    out = ot._run_subagent("email", "check inbox")
    assert is_error_result(out) is False
