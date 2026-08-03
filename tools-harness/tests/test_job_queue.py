"""
Tests for job_queue — lifecycle, checkpointing, retries, seen_ids.
Uses a temp DB so the real jobs.db is never touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jobs import job_queue


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "test_jobs.db"
    monkeypatch.setattr(job_queue, "DB_PATH", db)
    job_queue.init()
    yield db


class TestJobLifecycle:

    def test_enqueue_creates_job(self):
        jid = job_queue.enqueue("email_pipeline", {"sender": "a@b.com"})
        job = job_queue.get_job(jid)
        assert job is not None
        assert job["task_name"] == "email_pipeline"
        assert json.loads(job["params_json"]) == {"sender": "a@b.com"}
        assert job["status"] == "queued"

    def test_claim_next_returns_oldest_queued(self):
        jid1 = job_queue.enqueue("morning_briefing")
        jid2 = job_queue.enqueue("email_pipeline")
        claimed = job_queue.claim_next()
        assert claimed is not None
        assert claimed["id"] == jid1
        assert claimed["status"] == "running"  # returned dict must reflect the claim, not the pre-claim row

        # DB is updated to running
        job = job_queue.get_job(jid1)
        assert job["status"] == "running"

        # Second claim returns the next job
        claimed2 = job_queue.claim_next()
        assert claimed2["id"] == jid2

    def test_claim_next_empty_returns_none(self):
        assert job_queue.claim_next() is None

    def test_checkpoint_updates_step(self):
        jid = job_queue.enqueue("email_pipeline")
        job_queue.checkpoint(jid, "emails_fetched")
        job = job_queue.get_job(jid)
        assert job["current_step"] == "emails_fetched"

    def test_mark_done(self):
        jid = job_queue.enqueue("email_pipeline")
        job_queue.claim_next()
        job_queue.mark_done(jid)
        job = job_queue.get_job(jid)
        assert job["status"] == "done"

    def test_mark_failed_requeues_below_max_retries(self):
        jid = job_queue.enqueue("email_pipeline")
        job_queue.mark_failed(jid, "timeout")
        job = job_queue.get_job(jid)
        assert job["status"] == "queued"
        assert job["retries"] == 1

    def test_mark_failed_permanent_after_max_retries(self):
        jid = job_queue.enqueue("email_pipeline")
        for _ in range(3):
            job_queue.mark_failed(jid, "timeout")
        job = job_queue.get_job(jid)
        assert job["status"] == "failed"
        assert job["retries"] == 3

    def test_list_jobs_returns_most_recent_first(self):
        jid1 = job_queue.enqueue("morning_briefing")
        jid2 = job_queue.enqueue("email_pipeline")
        jobs = job_queue.list_jobs(limit=10)
        assert jobs[0]["id"] == jid2
        assert jobs[1]["id"] == jid1

    def test_list_jobs_filter_by_task_name(self):
        job_queue.enqueue("morning_briefing")
        job_queue.enqueue("email_pipeline")
        jobs = job_queue.list_jobs(limit=10, task_names=["morning_briefing"])
        assert len(jobs) == 1
        assert jobs[0]["task_name"] == "morning_briefing"


class TestSeenIds:

    def test_save_and_load_seen_ids(self):
        job_queue.save_seen_id("msg1")
        job_queue.save_seen_id("msg2")
        ids = job_queue.load_seen_ids()
        assert ids == {"msg1", "msg2"}

    def test_save_seen_id_idempotent(self):
        job_queue.save_seen_id("msg1")
        job_queue.save_seen_id("msg1")
        ids = job_queue.load_seen_ids()
        assert ids == {"msg1"}

    def test_load_seen_ids_empty(self):
        assert job_queue.load_seen_ids() == set()
