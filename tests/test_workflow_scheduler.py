import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

from store import workflow_store


def test_claim_due_workflows_advances_interval_schedule(monkeypatch, tmp_path):
    workflow_db = tmp_path / "workflow_state.db"
    monkeypatch.setattr("store.workflow_store.DB_PATH", workflow_db)

    workflow_store.init()

    due_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    workflow = workflow_store.create_workflow_instance(
        automation_id="watch_and_tell",
        task_name="watch_and_alert",
        config={"topic": "GPU prices"},
        schedule={"interval_seconds": 300},
        next_run_at=due_at,
    )

    claimed = workflow_store.claim_due_workflows()
    assert len(claimed) == 1
    assert claimed[0]["id"] == workflow["id"]

    updated = workflow_store.get_workflow_instance(workflow["id"])
    assert updated is not None
    assert updated["next_run_at"] is not None
    assert updated["next_run_at"] > due_at


def test_cron_schedule_computes_next_run(monkeypatch, tmp_path):
    workflow_db = tmp_path / "workflow_state.db"
    monkeypatch.setattr("store.workflow_store.DB_PATH", workflow_db)
    workflow_store.init()

    fixed_now = datetime(2026, 4, 10, 11, 58, tzinfo=timezone.utc)
    monkeypatch.setattr("store.workflow_store._utcnow", lambda: fixed_now)

    workflow = workflow_store.create_workflow_instance(
        automation_id="watch_and_tell",
        task_name="watch_and_alert",
        config={"topic": "Figma releases"},
        schedule={"cron": "0 8 * * *", "timezone": "America/New_York"},
    )

    assert workflow["next_run_at"] == "2026-04-10T12:00:00+00:00"


def test_claim_due_cron_workflow_advances_to_next_cron_slot(monkeypatch, tmp_path):
    workflow_db = tmp_path / "workflow_state.db"
    monkeypatch.setattr("store.workflow_store.DB_PATH", workflow_db)

    workflow_store.init()

    fixed_now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("store.workflow_store._utcnow", lambda: fixed_now)

    workflow = workflow_store.create_workflow_instance(
        automation_id="watch_and_tell",
        task_name="watch_and_alert",
        config={"topic": "GPU prices"},
        schedule={"cron": "*/5 * * * *", "timezone": "UTC"},
        next_run_at=fixed_now.isoformat(),
    )

    claimed = workflow_store.claim_due_workflows()
    assert len(claimed) == 1

    updated = workflow_store.get_workflow_instance(workflow["id"])
    assert updated is not None
    assert updated["next_run_at"] == "2026-04-10T12:05:00+00:00"


def test_cron_schedule_supports_named_weekdays(monkeypatch, tmp_path):
    workflow_db = tmp_path / "workflow_state.db"
    monkeypatch.setattr("store.workflow_store.DB_PATH", workflow_db)
    workflow_store.init()

    fixed_now = datetime(2026, 4, 10, 11, 58, tzinfo=timezone.utc)
    monkeypatch.setattr("store.workflow_store._utcnow", lambda: fixed_now)

    workflow = workflow_store.create_workflow_instance(
        automation_id="watch_and_tell",
        task_name="watch_and_alert",
        config={"topic": "Figma releases"},
        schedule={"cron": "0 8 * * mon-fri", "timezone": "America/New_York"},
    )

    assert workflow["next_run_at"] == "2026-04-10T12:00:00+00:00"


def test_seed_builtins_migrates_missing_cron_timezone(monkeypatch, tmp_path):
    workflow_db = tmp_path / "workflow_state.db"
    monkeypatch.setattr("store.workflow_store.DB_PATH", workflow_db)
    monkeypatch.setenv("USER_TIMEZONE", "America/New_York")
    workflow_store.init()

    workflow = workflow_store.create_workflow_instance(
        automation_id="briefing.morning",
        task_name="Morning briefing",
        schedule={"cron": "0 8 * * *"},
        dedupe_key="briefing.morning",
        next_run_at="2026-04-10T08:00:00+00:00",
    )

    workflow_store.seed_builtins()

    updated = workflow_store.get_workflow_instance(workflow["id"])
    assert updated is not None
    assert updated["schedule"] == {"cron": "0 8 * * *", "timezone": "America/New_York"}
