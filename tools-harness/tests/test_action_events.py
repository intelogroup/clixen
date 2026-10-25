"""Tool started/finished events, so the orb can animate the action itself.

The bunny used to animate off a scraped "⚙️ Running <tool>…" string in the model's
narration: it could not tell success from failure, and it fired for prose that merely
looked like a label. These tests pin the replacement contract.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import action_events  # noqa: E402


@pytest.fixture(autouse=True)
def clean_sinks():
    action_events.clear()
    yield
    action_events.clear()


def test_started_then_done_with_a_duration():
    seen = []
    action_events.add_sink(seen.append)
    action_events.publish("started", "observe")
    action_events.publish("done", "observe", ok=True, ms=42)

    assert [e["phase"] for e in seen] == ["started", "done"]
    assert seen[1]["ms"] == 42 and seen[1]["ok"] is True


def test_a_failed_action_is_reported_as_failed():
    seen = []
    action_events.add_sink(seen.append)
    action_events.publish("done", "act", ok=False, ms=7, error="stale element")
    assert seen[0]["ok"] is False and "stale" in seen[0]["error"]


def test_a_broken_sink_cannot_break_a_tool_call():
    def explode(_payload):
        raise RuntimeError("sink is down")

    action_events.add_sink(explode)
    good = []
    action_events.add_sink(good.append)
    action_events.publish("started", "observe")   # must not raise
    assert good and good[0]["tool"] == "observe"


def test_publishing_with_no_sink_is_a_no_op():
    action_events.publish("started", "observe")   # no sinks attached, no error


def test_the_registry_publishes_for_a_real_tool():
    from tools.registry import execute_tool

    seen = []
    action_events.add_sink(seen.append)
    execute_tool("get_current_time", {})

    phases = [(e["phase"], e["tool"]) for e in seen]
    assert ("started", "get_current_time") in phases
    assert ("done", "get_current_time") in phases


def test_removing_a_sink_stops_delivery():
    seen = []
    action_events.add_sink(seen.append)
    action_events.remove_sink(seen.append)
    action_events.publish("started", "observe")
    assert seen == []
