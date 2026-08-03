import os
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

from automation_catalog import AUTOMATIONS, build_dispatch_payload, list_automations
from clients.router import TASK_ROUTING
from jobs.worker import _build_query
from chat_ui import app


def test_catalog_exposes_ten_automations():
    items = list_automations()
    assert len(items) == len(AUTOMATIONS)
    assert {item["id"] for item in items} == {item["id"] for item in AUTOMATIONS}


def test_every_automation_maps_to_known_task():
    for automation in AUTOMATIONS:
        assert automation["task_name"] in TASK_ROUTING
        assert "primary_field" in automation
        assert automation["primary_field"]["key"] in automation["params"]


def test_build_dispatch_payload_merges_overrides():
    payload = build_dispatch_payload("watch_and_tell", {"topic": "Figma releases"})
    assert payload["task_name"] == "watch_and_alert"
    assert payload["params"]["topic"] == "Figma releases"


def test_worker_builds_queries_for_new_automation_tasks():
    task_params = {
        "inbox_autopilot": {"focus": "investor emails"},
        "calendar_copilot": {"window": "next 48 hours"},
        "admin_cleanup": {"focus": "expense receipts"},
        "watch_and_alert": {"topic": "GPU prices"},
        "browser_chore": {"goal": "prep a renewal form"},
        "family_logistics": {"household": "school pickup changes"},
        "founder_pipeline": {"focus": "pipeline updates"},
        "knowledge_to_action": {"source": "meeting notes"},
        "travel_mode": {"context": "airport departure"},
        "stubborn_reminder": {"task": "submit taxes"},
    }
    for task_name, params in task_params.items():
        query = _build_query(task_name, params)
        assert isinstance(query, str)
        assert len(query) > 20


def test_get_automations_route_returns_catalog():
    client = TestClient(app)
    with patch("chat_ui.automation_store.list_presets", return_value={}):
        response = client.get("/automations")
    assert response.status_code == 200
    data = response.json()
    assert len(data["automations"]) == len(AUTOMATIONS)


def test_run_automation_dispatches_queue_job():
    client = TestClient(app)
    with patch("chat_ui.job_queue.enqueue", return_value="job-123") as mock_enqueue:
        response = client.post("/automations/inbox_autopilot/run", json={"overrides": {"focus": "vip senders"}})
    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == "job-123"
    assert data["status"] == "queued"
    mock_enqueue.assert_called_once_with("inbox_autopilot", {"focus": "vip senders"})


def test_run_automation_404_for_unknown_id():
    client = TestClient(app)
    response = client.post("/automations/not-real/run", json={"overrides": {}})
    assert response.status_code == 404


def test_get_automations_route_merges_presets():
    client = TestClient(app)
    with patch("chat_ui.automation_store.list_presets", return_value={"watch_and_tell": {"topic": "Figma releases"}}):
        response = client.get("/automations")
    assert response.status_code == 200
    data = response.json()
    watch = next(item for item in data["automations"] if item["id"] == "watch_and_tell")
    assert watch["params"]["topic"] == "Figma releases"


def test_save_automation_preset_route():
    client = TestClient(app)
    with patch("chat_ui.automation_store.save_preset") as mock_save:
        response = client.post("/automations/watch_and_tell/preset", json={"params": {"topic": "earnings calls"}})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "saved"
    mock_save.assert_called_once_with("watch_and_tell", {"topic": "earnings calls"})


def test_recent_automation_runs_route_maps_task_to_automation():
    client = TestClient(app)
    fake_runs = [
        {
            "id": "job-1",
            "task_name": "inbox_autopilot",
            "params_json": "{}",
            "status": "running",
            "current_step": "started",
            "retries": 0,
            "last_error": None,
            "created_at": "2026-04-10T00:00:00+00:00",
            "updated_at": "2026-04-10T00:00:10+00:00",
        }
    ]
    with patch("chat_ui.job_queue.list_jobs", return_value=fake_runs):
        response = client.get("/automations/runs")
    assert response.status_code == 200
    data = response.json()
    assert data["runs"][0]["automation_id"] == "inbox_autopilot"
