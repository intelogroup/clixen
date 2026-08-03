"""websearch.search() must record a trace_store entry when given a run_id.

2026-07-10/11: search() never wrote to trace_store at all, so every
ask_web_search call was structurally invisible to orchestrator_tools.py's
_run_subagent() envelope -- it always reported status=degraded tools=none,
*even when the search succeeded*. The orchestrator's own system prompt says
not to trust a degraded subagent, so it kept re-querying the same fact with
reworded phrasing (measured live: ~20s across 4-5 near-duplicate calls for
one weather question instead of ~7s for one). Fixed by recording a trace
entry (tool=ask_web_search) at every return point in search().
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools.websearch as ws


def test_search_records_trace_entry_on_clarification_path(monkeypatch):
    # The guard-clarification early return is the cheapest path to hit
    # without mocking the whole search/rerank/summarize pipeline.
    monkeypatch.setattr(ws, "_guard_check", lambda q: "Which city's weather do you want?")

    recorded = []
    import store.trace_store as trace_store
    monkeypatch.setattr(trace_store, "record", lambda run_id, entry: recorded.append((run_id, entry)))

    result = ws.search("what's the weather", run_id="test-run-123")

    assert result == "Which city's weather do you want?"
    assert len(recorded) == 1
    run_id, entry = recorded[0]
    assert run_id == "test-run-123"
    assert entry["tool"] == "ask_web_search"
    assert entry["error"] is None


def test_search_without_run_id_does_not_touch_trace_store(monkeypatch):
    monkeypatch.setattr(ws, "_guard_check", lambda q: "clarify?")

    import store.trace_store as trace_store
    calls = []
    monkeypatch.setattr(trace_store, "record", lambda *a, **k: calls.append(1))

    ws.search("what's the weather")  # no run_id
    assert calls == []
