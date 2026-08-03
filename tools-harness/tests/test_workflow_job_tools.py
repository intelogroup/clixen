import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.registry import execute_tool
from store import workflow_store
from jobs import job_queue

@pytest.fixture(autouse=True)
def setup_test_dbs(tmp_path, monkeypatch):
    # Mock database paths to use temp directory
    jobs_db = tmp_path / "jobs.db"
    workflow_db = tmp_path / "workflow_state.db"

    monkeypatch.setattr("jobs.job_queue.DB_PATH", jobs_db)
    monkeypatch.setattr("store.workflow_store.DB_PATH", workflow_db)

    # Initialize databases
    job_queue.init()
    workflow_store.init()

def test_workflow_tools():
    # 1. Create a workflow
    create_res = execute_tool("create_workflow", {
        "workflow_id": "test_wf_1",
        "automation_id": "health_check",
        "task_name": "Test Task",
        "schedule_type": "cron",
        "schedule_expr": "*/5 * * * *",
    })
    assert "created successfully" in create_res

    # 2. List workflows
    list_res = execute_tool("list_workflows", {})
    assert "test_wf_1" in list_res
    assert "Test Task" in list_res

    # 3. Pause / resume via automation_tools equivalents
    pause_res = execute_tool("pause_automation", {"automation_id": "test_wf_1"})
    assert "paused" in pause_res

    list_res_paused = execute_tool("list_workflows", {})
    assert "Status: paused" in list_res_paused

    resume_res = execute_tool("resume_automation", {"automation_id": "test_wf_1"})
    assert "resumed" in resume_res

    delete_res = execute_tool("delete_workflow_permanently", {"workflow_id": "test_wf_1"})
    assert "permanently deleted" in delete_res

    list_res_deleted = execute_tool("list_workflows", {})
    assert "No workflow instances found." in list_res_deleted
