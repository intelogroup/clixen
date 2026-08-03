"""
Tests for workflow_store — seed_builtins behaviour and seen_ids persistence.
Uses a temp DB so the real workflow_state.db is never touched.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Make tools-harness importable
sys.path.insert(0, str(Path(__file__).parent.parent))

import store.workflow_store as ws


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Redirect all DB access to a throwaway file."""
    db = tmp_path / "test_workflow.db"
    monkeypatch.setattr(ws, "DB_PATH", db)
    ws.init()
    yield db


# ---------------------------------------------------------------------------
# seed_builtins — must not re-activate paused instances
# ---------------------------------------------------------------------------

def test_seed_builtins_does_not_reactivate_paused():
    """Pausing a builtin must survive a seed_builtins() call (restart sim)."""
    ws.seed_builtins()

    # Grab the first builtin and pause it
    instances = ws.list_workflow_instances()
    assert instances, "seed must create at least one instance"
    inst = instances[0]
    ws.update_workflow_instance(inst["id"], status="paused")

    # Simulate restart — call seed again
    ws.seed_builtins()

    refreshed = ws.get_workflow_instance(inst["id"])
    assert refreshed["status"] == "paused", (
        "seed_builtins() must not flip paused instances back to active"
    )


def test_seed_builtins_idempotent():
    """Calling seed_builtins() twice must not duplicate rows."""
    ws.seed_builtins()
    ws.seed_builtins()
    instances = ws.list_workflow_instances()
    ids = [i["id"] for i in instances]
    assert len(ids) == len(set(ids)), "duplicate workflow instances after double seed"


def test_seed_builtins_creates_expected_workflows():
    ws.seed_builtins()
    keys = {i["dedupe_key"] for i in ws.list_workflow_instances()}
    expected = {w["dedupe_key"] for w in ws._BUILTIN_WORKFLOWS}
    assert expected == keys


# ---------------------------------------------------------------------------
# seen_ids persistence — must survive update round-trip
# ---------------------------------------------------------------------------

def test_seen_ids_persist_across_update():
    ws.seed_builtins()
    inst = next(
        i for i in ws.list_workflow_instances()
        if i["automation_id"] == "email.watch_sender"
    )
    new_ids = ["aaa", "bbb", "ccc"]
    ws.update_workflow_instance(
        inst["id"],
        config={**inst["config"], "seen_ids": new_ids},
    )
    refreshed = ws.get_workflow_instance(inst["id"])
    assert refreshed["config"]["seen_ids"] == new_ids


def test_seen_ids_not_wiped_by_seed():
    """seed_builtins() after seen_ids update must not reset config."""
    ws.seed_builtins()
    inst = next(
        i for i in ws.list_workflow_instances()
        if i["automation_id"] == "email.watch_sender"
    )
    saved_ids = ["id1", "id2", "id3"]
    ws.update_workflow_instance(
        inst["id"],
        config={**inst["config"], "seen_ids": saved_ids},
    )

    # Simulate restart
    ws.seed_builtins()

    refreshed = ws.get_workflow_instance(inst["id"])
    assert refreshed["config"]["seen_ids"] == saved_ids, (
        "seed_builtins() must not wipe seen_ids on existing instances"
    )


# ---------------------------------------------------------------------------
# email.watch_sender — empty senders guard
# ---------------------------------------------------------------------------

def test_watch_sender_returns_error_when_no_senders(monkeypatch):
    """Handler must return success=False immediately if senders list is empty and no env var."""
    monkeypatch.delenv("WATCHED_SENDERS", raising=False)
    monkeypatch.delenv("EMAIL_WATCH_SENDERS", raising=False)

    ws.seed_builtins()
    inst = next(
        i for i in ws.list_workflow_instances()
        if i["automation_id"] == "email.watch_sender"
    )
    # Ensure config senders is empty
    ws.update_workflow_instance(inst["id"], config={**inst["config"], "senders": []})
    inst = ws.get_workflow_instance(inst["id"])

    from jobs.handlers.email_watch_sender import handle
    result = handle(inst)
    assert result["success"] is False
    assert result["items_processed"] == 0
    assert "no senders" in result.get("error", "")


# ---------------------------------------------------------------------------
# Claim and schedule progression tests
# ---------------------------------------------------------------------------

def test_claim_due_workflows_updates_schedules_and_last_run_at():
    """Verify that claiming a due workflow preserves the old scheduled time as last_run_at and computes next_run_at."""
    # Create a workflow scheduled to run every 60 seconds, with next_run_at in the past
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    due_time = (now - timedelta(seconds=10)).isoformat()
    
    inst = ws.create_workflow_instance(
        automation_id="test.interval",
        task_name="Interval test",
        schedule={"interval_seconds": 60},
        next_run_at=due_time,
        status="active",
    )
    
    # Claim it
    claimed = ws.claim_due_workflows(limit=5)
    assert len(claimed) == 1
    c = claimed[0]
    assert c["id"] == inst["id"]
    
    # last_run_at in returned item must be the scheduled due time
    assert c["last_run_at"] == due_time
    
    # next_run_at in returned item must be advanced (approx +60s from now)
    assert c["next_run_at"] is not None
    next_dt = ws._parse_iso(c["next_run_at"])
    assert next_dt > now
    
    # Simulate worker updating the workflow after successful run
    ws.update_workflow_instance(
        c["id"],
        last_run_at=c.get("last_run_at"),
        last_result={"success": True},
    )
    
    # Retrieve from DB and verify fields
    refreshed = ws.get_workflow_instance(c["id"])
    assert refreshed["last_run_at"] == due_time
    assert refreshed["next_run_at"] == c["next_run_at"]
    assert refreshed["last_result"] == {"success": True}


def test_claim_due_workflows_does_not_loop_catchup():
    """Verify that a workflow whose schedule missed multiple runs (e.g. cron every hour, 5 hours overdue) does not catch up in a loop, but advances to the next future scheduled run."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    
    # Scheduled to run every hour, next_run_at is 5 hours in the past
    five_hours_ago = (now - timedelta(hours=5)).replace(minute=0, second=0, microsecond=0)
    due_time = five_hours_ago.isoformat()
    
    inst = ws.create_workflow_instance(
        automation_id="test.cron_catchup",
        task_name="Cron catchup test",
        schedule={"cron": "0 * * * *", "timezone": "UTC"},
        next_run_at=due_time,
        status="active",
    )
    
    # Claim it once
    claimed = ws.claim_due_workflows(limit=5)
    assert len(claimed) == 1
    c = claimed[0]
    
    # The new next_run_at must be in the future relative to "now" (not just after five_hours_ago)
    assert c["next_run_at"] is not None
    next_dt = ws._parse_iso(c["next_run_at"])
    assert next_dt > now
    
    # Claiming again immediately should yield nothing because the next run is in the future
    claimed_again = ws.claim_due_workflows(limit=5)
    assert len(claimed_again) == 0


def test_worker_workflow_failure_generates_notification():
    """Verify that when a handler returns success=False, a persistent error notification is added."""
    from unittest.mock import patch, MagicMock
    import jobs.worker as worker
    
    inst = ws.create_workflow_instance(
        automation_id="test.failing_handler",
        task_name="Failing Handler Test",
        schedule={"interval_seconds": 60},
        next_run_at=ws._utcnow().isoformat(),
        status="active",
    )
    
    mock_dispatch = MagicMock(return_value={"success": False, "error": "API rate limit reached"})
    with patch("jobs.handler_registry.dispatch", mock_dispatch):
        worker._poll_workflow_store()
        
    notifs = ws.list_notifications()
    assert len(notifs) == 1
    n = notifs[0]
    assert n["level"] == "error"
    assert "Failing Handler Test" in n["message"]
    assert "API rate limit reached" in n["message"]
    assert n["source"] == "workflow.test.failing_handler"


def test_worker_workflow_exception_generates_notification():
    """Verify that when a handler raises an exception, a persistent error notification is added."""
    from unittest.mock import patch, MagicMock
    import jobs.worker as worker
    
    inst = ws.create_workflow_instance(
        automation_id="test.excepting_handler",
        task_name="Excepting Handler Test",
        schedule={"interval_seconds": 60},
        next_run_at=ws._utcnow().isoformat(),
        status="active",
    )
    
    mock_dispatch = MagicMock(side_effect=RuntimeError("Connection refused"))
    with patch("jobs.handler_registry.dispatch", mock_dispatch):
        worker._poll_workflow_store()
        
    notifs = ws.list_notifications()
    assert len(notifs) == 1
    n = notifs[0]
    assert n["level"] == "error"
    assert "Excepting Handler Test" in n["message"]
    assert "Connection refused" in n["message"]
    assert n["source"] == "workflow.test.excepting_handler"



