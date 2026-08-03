"""Golden-query suite: evaluation logic + handler plumbing (hermetic)."""
import pytest

from jobs.handlers import golden_queries as gq


# ── _evaluate ────────────────────────────────────────────────────────────────

def test_must_call_pass():
    spec = {"must_call": {"ask_email_agent"}}
    trace = [{"tool": "ask_email_agent"}, {"tool": "ask_calendar_agent"}]
    assert gq._evaluate(spec, "answer", trace, 10) == []


def test_must_call_fail_names_missing_tool():
    spec = {"must_call": {"ask_email_agent"}}
    failures = gq._evaluate(spec, "answer", [{"tool": "ask_calendar_agent"}], 10)
    assert failures and "ask_email_agent" in failures[0]


def test_must_call_any_threshold():
    spec = {"must_call_any": {"a", "b", "c"}, "min_sources": 2}
    assert gq._evaluate(spec, "x", [{"tool": "a"}, {"tool": "b"}], 5) == []
    assert gq._evaluate(spec, "x", [{"tool": "a"}], 5)


def test_forbid_escalation():
    spec = {"forbid_escalation": True}
    trace = [{"tool": "ask_email_agent"}, {"tool": "_model_escalation", "escalated": True}]
    failures = gq._evaluate(spec, "x", trace, 5)
    assert failures == ["model escalation fired"]


def test_answer_pattern_and_empty_answer():
    assert gq._evaluate({"answer_must_not_match": r"as an ai"}, "As an AI, I cannot", [], 5)
    assert "empty answer" in gq._evaluate({}, "   ", [], 5)


def test_latency_budget():
    assert gq._evaluate({"max_seconds": 30}, "x", [], 31)
    assert gq._evaluate({"max_seconds": 30}, "x", [], 29) == []


# ── handle ───────────────────────────────────────────────────────────────────

@pytest.fixture
def stubbed_run(monkeypatch):
    """harness.run returns a canned answer; trace pre-seeded per run_id."""
    import harness
    from store import trace_store

    traces = {}

    def fake_run(query, chat_id=None, run_id=None, **kw):
        traces[run_id] = [
            {"tool": t} for t in ("ask_email_agent", "ask_calendar_agent", "ask_tasks_agent")
        ]
        return f"answer for {query}", "model", "orchestrator"

    def fake_pipeline(intent, query, chat_id=None, run_id=None):
        traces[run_id] = [{"tool": "list_emails"}]
        return f"pipeline answer for {query}"

    monkeypatch.setattr(harness, "run", fake_run)
    monkeypatch.setattr(harness, "_execute_intent_pipeline", fake_pipeline)
    monkeypatch.setattr(trace_store, "get_trace", lambda rid: traces.get(rid, []))
    return traces


def test_handle_all_pass(stubbed_run):
    result = gq.handle({"config": {}})
    assert result["success"] is True
    assert result["passed"] == len(gq.GOLDEN_QUERIES)
    assert result["failed"] == 0
    assert "error" not in result


def test_handle_reports_failures(monkeypatch):
    import harness
    from store import trace_store
    monkeypatch.setattr(harness, "run", lambda **kw: ("nothing found", "m", "o"))
    monkeypatch.setattr(trace_store, "get_trace", lambda rid: [])  # zero tools called

    result = gq.handle({"config": {}})
    assert result["success"] is False
    assert result["failed"] > 0
    assert result["error"]  # summary string for _notify_workflow_failure
    assert all("name" in d and d["failures"] for d in result["details"])


# ── cross-file drift guards ─────────────────────────────────────────────────

def test_commitment_source_set_matches_harness():
    """golden_queries._COMMITMENT must not drift from harness._COMMITMENT_SOURCES —
    the whole point of this suite is to catch the class of bug where one of the
    two sets misses a source the other has (the 2026-08-01 iMessage gap)."""
    import harness
    assert gq._COMMITMENT == harness._COMMITMENT_SOURCES


def test_schedule_fanout_prompt_names_every_commitment_source():
    """The orchestrator prompt fragment must literally name every tool in
    _COMMITMENT_SOURCES — a source added to the set but not the prompt text
    means the LLM is never told to call it, silently reopening the same gap."""
    import harness
    from skills_data.orchestrator_fragments.schedule_fanout import FRAGMENT
    for tool in harness._COMMITMENT_SOURCES:
        assert tool in FRAGMENT, f"{tool} missing from schedule_fanout FRAGMENT text"


def test_handle_survives_run_exception(monkeypatch):
    import harness
    def boom(**kw):
        raise RuntimeError("cloud down")
    monkeypatch.setattr(harness, "run", boom)

    result = gq.handle({"config": {}})
    assert result["success"] is False
    assert result["failed"] == len(gq.GOLDEN_QUERIES)
    assert "raised: cloud down" in result["details"][0]["failures"][0]
