"""M4: missed-run catch-up for scheduled workflows (the system was down)."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jobs import job_queue
from store import workflow_store


@pytest.fixture(autouse=True)
def _dbs(tmp_path, monkeypatch):
    monkeypatch.setattr("jobs.job_queue.DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr("store.workflow_store.DB_PATH", tmp_path / "wf.db")
    job_queue.init()
    workflow_store.init()


def _instance(schedule, *, next_run_at):
    return workflow_store.create_workflow_instance(
        automation_id="a1", task_name="daily", schedule=schedule,
        config={}, next_run_at=next_run_at)


def test_missed_fire_times_counted_between_next_run_and_now():
    now = datetime.now(timezone.utc)
    past = (now - timedelta(hours=5)).isoformat()
    inst = _instance({"cron": "0 * * * *"}, next_run_at=past)
    missed = workflow_store.missed_fire_times(inst, now=now)
    assert len(missed) == 5
    assert all(m < now.isoformat() for m in missed)


def test_missed_fire_times_capped_and_chronological():
    now = datetime.now(timezone.utc)
    past = (now - timedelta(days=30)).isoformat()
    inst = _instance({"cron": "0 * * * *"}, next_run_at=past)
    missed = workflow_store.missed_fire_times(inst, now=now, cap=3)
    assert len(missed) == 3
    assert missed == sorted(missed), "oldest first"


def test_interval_schedules_supported():
    now = datetime.now(timezone.utc)
    past = (now - timedelta(minutes=30)).isoformat()
    inst = _instance({"interval_seconds": 600}, next_run_at=past)
    missed = workflow_store.missed_fire_times(inst, now=now)
    assert len(missed) == 3  # 30 min / 10 min = 3 slots


def test_no_missed_when_next_run_is_future():
    now = datetime.now(timezone.utc)
    future = (now + timedelta(hours=1)).isoformat()
    inst = _instance({"cron": "0 * * * *"}, next_run_at=future)
    assert workflow_store.missed_fire_times(inst, now=now) == []


def test_catch_up_enqueues_jobs_and_advances_schedule():
    now = datetime.now(timezone.utc)
    past = (now - timedelta(hours=2)).isoformat()
    inst = _instance({"cron": "0 * * * *"}, next_run_at=past)
    jobs = workflow_store.catch_up_missed(inst["id"], now=now, cap=2)
    assert len(jobs) == 2
    queued = [j for j in jobs if j["status"] == "queued"]
    assert len(queued) == 2
    # schedule advanced past now — the catch-up does not re-arm the past slot
    fresh = workflow_store.get_workflow_instance(inst["id"])
    assert fresh["next_run_at"] > now.isoformat()


def test_catch_up_is_a_noop_when_nothing_missed():
    now = datetime.now(timezone.utc)
    inst = _instance({"cron": "0 * * * *"},
                     next_run_at=(now + timedelta(hours=1)).isoformat())
    assert workflow_store.catch_up_missed(inst["id"], now=now) == []
