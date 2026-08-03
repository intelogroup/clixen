"""
Regression test for the 2026-07-11 duplicate-workflow-instance gap:
create_workflow never checked whether an active instance of the same
automation_id already existed before inserting a new one — confirmed live,
"create a daily morning briefing workflow" was about to create a second
briefing.morning instance alongside an already-active one, which would have
double-sent the briefing. Fix is code-level (not prompt-only): create_workflow
now blocks a same-automation_id, same/no-params duplicate unless force=true,
and list_available_pipelines annotates each pipeline with its active-instance
count.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from store import workflow_store
from tools.workflow_job_tools import create_workflow, list_available_pipelines
from conftest import safe_delete_test_workflow


def _cleanup(*wf_ids):
    for wf_id in wf_ids:
        safe_delete_test_workflow(wf_id)


def test_create_workflow_blocks_empty_params_duplicate():
    workflow_store.init()
    wf1, wf2 = "test_dedup_a", "test_dedup_b"
    _cleanup(wf1, wf2)
    try:
        first = create_workflow({
            "workflow_id": wf1, "automation_id": "health_check",
            "task_name": "first", "schedule_type": "interval", "schedule_expr": "300",
            "force": True,  # a real "health_check" pipeline instance may already be active
        })
        assert "Error" not in first, first

        second = create_workflow({
            "workflow_id": wf2, "automation_id": "health_check",
            "task_name": "second", "schedule_type": "interval", "schedule_expr": "300",
        })
        assert "Error" in second
        assert wf1 in second or "first" in second
        assert workflow_store.get_workflow_instance(wf2) is None
    finally:
        _cleanup(wf1, wf2)


def test_create_workflow_force_bypasses_duplicate_block():
    workflow_store.init()
    wf1, wf2 = "test_dedup_force_a", "test_dedup_force_b"
    _cleanup(wf1, wf2)
    try:
        create_workflow({
            "workflow_id": wf1, "automation_id": "health_check",
            "task_name": "first", "schedule_type": "interval", "schedule_expr": "300",
        })
        second = create_workflow({
            "workflow_id": wf2, "automation_id": "health_check",
            "task_name": "second", "schedule_type": "interval", "schedule_expr": "300",
            "force": True,
        })
        assert "Error" not in second, second
        assert workflow_store.get_workflow_instance(wf2) is not None
    finally:
        _cleanup(wf1, wf2)


def test_create_workflow_allows_differing_params():
    workflow_store.init()
    wf1, wf2 = "test_dedup_diff_a", "test_dedup_diff_b"
    _cleanup(wf1, wf2)
    try:
        create_workflow({
            "workflow_id": wf1, "automation_id": "health_check",
            "task_name": "first", "schedule_type": "interval", "schedule_expr": "300",
            "params": {"senders": ["alice"]},
        })
        second = create_workflow({
            "workflow_id": wf2, "automation_id": "health_check",
            "task_name": "second", "schedule_type": "interval", "schedule_expr": "300",
            "params": {"senders": ["bob"]},
        })
        assert "Error" not in second, second
        assert "Note:" in second
        assert workflow_store.get_workflow_instance(wf2) is not None
    finally:
        _cleanup(wf1, wf2)


def test_list_available_pipelines_shows_active_instance_count():
    import jobs.worker as worker
    worker._setup_handler_registry()
    workflow_store.init()

    # study.usmle_daily is a registered builtin pipeline (handler_registry) — use
    # a distinct params dict so this test's instance doesn't collide with any
    # real seeded/live one via the dedup block added above.
    automation_id = "study.usmle_daily"
    before = len(workflow_store.list_workflow_instances(automation_id=automation_id, status="active"))

    wf1 = "test_dedup_discovery_a"
    _cleanup(wf1)
    try:
        result = create_workflow({
            "workflow_id": wf1, "automation_id": automation_id,
            "task_name": "discovery test", "schedule_type": "interval", "schedule_expr": "300",
            "params": {"_test_marker": "test_dedup_discovery_a"}, "force": True,
        })
        assert "Error" not in result, result

        out = list_available_pipelines({})
        lines = [ln for ln in out.splitlines() if ln.startswith(f"• {automation_id}")]
        assert lines, out
        assert f"{before + 1} active instance" in lines[0], lines[0]
    finally:
        _cleanup(wf1)
