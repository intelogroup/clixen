"""The durable-step seam keeps exactly the promises a resume would need."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store import step_journal as sj  # noqa: E402


@pytest.fixture(autouse=True)
def temp_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(sj, "_DB_PATH", tmp_path / "steps.sqlite")
    yield


def test_steps_are_ordered_and_round_trip_as_json():
    sj.record_next("run-1", "tool_call", {"tool": "observe"})
    sj.record_next("run-1", "tool_call", {"tool": "act"}, outcome="ok")

    steps = sj.load("run-1")
    assert [s["step_index"] for s in steps] == [0, 1]
    assert [s["payload"]["tool"] for s in steps] == ["observe", "act"]
    assert steps[1]["outcome"] == "ok"


def test_first_writer_wins():
    assert sj.record("run-2", 0, "tool_call", {"tool": "a"}) is True
    assert sj.record("run-2", 0, "tool_call", {"tool": "b"}) is False
    assert sj.load("run-2")[0]["payload"]["tool"] == "a"


def test_a_non_json_payload_is_refused_loudly():
    class Opaque:
        pass

    with pytest.raises(ValueError):
        sj.record("run-3", 0, "tool_call", {"obj": Opaque()})
    assert sj.load("run-3") == []


def test_a_step_needs_a_run_id():
    with pytest.raises(ValueError):
        sj.record("", 0, "tool_call", {})


def test_runs_are_independent_and_listed_newest_first():
    sj.record_next("run-a", "tool_call", {"tool": "x"})
    sj.record_next("run-b", "tool_call", {"tool": "y"})
    assert sj.next_index("run-a") == 1
    assert sj.next_index("run-b") == 1
    assert set(sj.run_ids()) == {"run-a", "run-b"}


def test_registry_journals_only_when_a_run_id_is_in_scope(monkeypatch, tmp_path):
    """No run id → no journal. With one → the call appears as a step."""
    from log_config import CURRENT_RUN_ID
    from tools.registry import execute_tool

    monkeypatch.setenv("CLIXEN_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / ".clixen-vault").write_text("1")

    token = CURRENT_RUN_ID.set("")
    try:
        execute_tool("workspace_list", {})
        assert sj.run_ids() == []
    finally:
        CURRENT_RUN_ID.reset(token)

    token = CURRENT_RUN_ID.set("run-journal")
    try:
        execute_tool("workspace_list", {})
    finally:
        CURRENT_RUN_ID.reset(token)
    steps = sj.load("run-journal")
    assert steps and steps[0]["payload"]["tool"] == "workspace_list"
    assert steps[0]["payload"]["action_class"] == "read"
