"""
Regression test for the 2026-07-11 create_workflow schedule-key bug: the tool
used to write {"type": "cron", "expr": ...} but workflow_store only ever reads
"cron"/"interval_seconds" keys, so next_run_at silently stayed None and
claim_due_workflows() (WHERE next_run_at IS NOT NULL) never picked the
workflow up — "created successfully" but dead on arrival.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from store import workflow_store
from tools.workflow_job_tools import create_workflow
from conftest import safe_delete_test_workflow


def test_create_workflow_cron_sets_next_run_at():
    workflow_store.init()
    wf_id = "test_cron_schedule_regression"
    safe_delete_test_workflow(wf_id)
    try:
        result = create_workflow({
            "workflow_id": wf_id,
            "automation_id": "health_check",
            "task_name": "regression test",
            "schedule_type": "cron",
            "schedule_expr": "0 8 * * *",
            "force": True,  # a real "health_check" pipeline instance may already be active
        })
        assert "Error" not in result, result
        inst = workflow_store.get_workflow_instance(wf_id)
        assert inst["next_run_at"] is not None, "next_run_at silently None — workflow will never fire"
        assert inst["schedule"]["cron"] == "0 8 * * *"
    finally:
        safe_delete_test_workflow(wf_id)


def test_create_workflow_interval_sets_next_run_at():
    workflow_store.init()
    wf_id = "test_interval_schedule_regression"
    safe_delete_test_workflow(wf_id)
    try:
        result = create_workflow({
            "workflow_id": wf_id,
            "automation_id": "health_check",
            "task_name": "regression test",
            "schedule_type": "interval",
            "schedule_expr": "300",
            "force": True,  # a real "health_check" pipeline instance may already be active
        })
        assert "Error" not in result, result
        inst = workflow_store.get_workflow_instance(wf_id)
        assert inst["next_run_at"] is not None
        assert inst["schedule"]["interval_seconds"] == 300
    finally:
        safe_delete_test_workflow(wf_id)


def test_create_workflow_rejects_malformed_cron():
    workflow_store.init()
    wf_id = "test_bad_cron_regression"
    safe_delete_test_workflow(wf_id)
    try:
        result = create_workflow({
            "workflow_id": wf_id,
            "automation_id": "health_check",
            "task_name": "regression test",
            "schedule_type": "cron",
            "schedule_expr": "not a cron",
        })
        assert "Error" in result
        assert workflow_store.get_workflow_instance(wf_id) is None
    finally:
        safe_delete_test_workflow(wf_id)


def test_create_automation_rejects_malformed_cron():
    from tools.automation_tools import create_automation

    result = create_automation({
        "name": "regression test bad cron",
        "trigger_type": "schedule",
        "action_type": "notification",
        "schedule": {"cron": "not a cron"},
    })
    assert "Error" in result
