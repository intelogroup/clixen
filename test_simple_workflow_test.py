#!/usr/bin/env python3
"""
Simple test to verify workflow job tools work correctly.
This simulates what the existing test_workflow_job_tools.py does.
"""
import os
import sys
import pytest
import json
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools-harness"))

# Import the registry tool execution function
from tools.registry import execute_tool

# Define a simple fixture similar to test_workflow_job_tools.py
@pytest.fixture(autouse=True)
def setup_test_dbs(tmp_path, monkeypatch):
    # Mock database paths to use temp directory
    jobs_db = tmp_path / "jobs.db"
    workflow_db = tmp_path / "workflow_state.db"
    
    # Set monkeypatch on the actual module paths
    import jobs.job_queue as jq_module
    import store.workflow_store as ws_module
    import tools.workflow_job_tools as tjt_module
    
    monkeypatch.setattr(jq_module, "DB_PATH", jobs_db)
    monkeypatch.setattr(ws_module, "DB_PATH", workflow_db)
    monkeypatch.setattr(tjt_module, "JOBS_DB_PATH", jobs_db)
    
    # Initialize databases
    jq_module.init()
    ws_module.init()

def test_workflow_job_tools():
    """Test basic workflow job tool functionality."""
    print("Testing workflow job tools...")

    # Test creating a workflow
    create_result = execute_tool("create_workflow", {
        "workflow_id": "test_simple",
        "automation_id": "test_automation",
        "task_name": "Simple Test Workflow",
        "schedule_type": "cron",
        "schedule_expr": "* * * * *",
        "status": "active",
    })

    assert "created successfully" in create_result.lower(), f"Create failed: {create_result}"
    print(f"✓ Workflow created: {create_result}")

    # Test listing workflows
    list_result = execute_tool("list_workflows", {"limit": 20})
    assert "test_simple" in list_result, f"Workflow not found in list: {list_result}"
    assert "Simple Test Workflow" in list_result, f"Workflow name not found in list: {list_result}"
    print(f"✓ Workflow listed")

    # Test pausing workflow
    pause_result = execute_tool("pause_workflow", {"workflow_id": "test_simple"})
    assert "paused successfully" in pause_result.lower(), f"Pause failed: {pause_result}"
    print(f"✓ Workflow paused: {pause_result}")

    # Test resuming workflow
    resume_result = execute_tool("resume_workflow", {"workflow_id": "test_simple"})
    assert "resumed successfully" in resume_result.lower(), f"Resume failed: {resume_result}"
    print(f"✓ Workflow resumed: {resume_result}")

    # Test triggering workflow
    trigger_result = execute_tool("trigger_workflow", {"workflow_id": "test_simple"})
    assert "triggered successfully" in trigger_result.lower(), f"Trigger failed: {trigger_result}"
    print(f"✓ Workflow triggered: {trigger_result}")

    # Test deleting workflow
    delete_result = execute_tool("delete_workflow", {"workflow_id": "test_simple"})
    assert "deleted successfully" in delete_result.lower(), f"Delete failed: {delete_result}"
    print(f"✓ Workflow deleted: {delete_result}")

    # Verify deletion
    list_after_delete = execute_tool("list_workflows", {"limit": 20})
    assert "test_simple" not in list_after_delete, f"Workflow still exists after deletion: {list_after_delete}"
    print(f"✓ Workflow deletion verified: No workflow with ID 'test_simple' found")

    print("\n✅ All workflow job tools tests PASSED!")

if __name__ == "__main__":
    # Run the test function directly
    test_workflow_job_tools()
