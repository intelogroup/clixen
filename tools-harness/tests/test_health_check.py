"""Tests for jobs/handlers/health_check.py — workflow health scan."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jobs.handlers import health_check
import store.workflow_store as real_store
import jobs.handler_registry as real_registry


def _mk(**overrides):
    base = {
        "id": "wf-1",
        "task_name": "tech_brief",
        "automation_id": "tech_brief",
        "status": "active",
        "last_run_at": "2026-07-14T00:00:00+00:00",
        "next_run_at": "2099-01-01T00:00:00+00:00",
        "last_result": {"success": True},
        "config": {},
    }
    base.update(overrides)
    return base


@pytest.fixture
def patched(monkeypatch):
    """Patch workflow_store + handler_registry attributes health_check.handle() calls."""
    monkeypatch.setattr(real_store, "get_workflow_instance", lambda wid: None)
    monkeypatch.setattr(real_store, "list_workflow_instances", lambda limit=1000: [])
    monkeypatch.setattr(real_store, "update_workflow_instance", lambda *a, **k: None)
    monkeypatch.setattr(real_registry, "registered_ids", lambda: {"tech_brief"})
    return real_store, real_registry


def test_no_workflows_found(patched):
    store_mod, _ = patched
    result = health_check.handle(_mk(id="hc-own", config={}))
    assert result["success"] is True
    assert result["checked"] == 0
    assert result["detail"] == "no workflows found"


def test_healthy_workflow_reported_only_with_include_ok(monkeypatch, patched):
    store_mod, _ = patched
    monkeypatch.setattr(store_mod, "list_workflow_instances", lambda limit=1000: [_mk()])
    result = health_check.handle(_mk(id="hc-own", config={}))
    assert result["problems"] == 0
    assert result["workflows"] == []  # problems-only by default

    result_ok = health_check.handle(_mk(id="hc-own", config={"include_ok": True}))
    assert len(result_ok["workflows"]) == 1


def test_unregistered_handler_flagged(monkeypatch, patched):
    store_mod, registry_mod = patched
    monkeypatch.setattr(store_mod, "list_workflow_instances", lambda limit=1000: [_mk(automation_id="ghost")])
    result = health_check.handle(_mk(id="hc-own", config={}))
    assert result["problems"] == 1
    assert result["workflows"][0]["issue"] == "no handler registered"


def test_bad_status_flagged(monkeypatch, patched):
    store_mod, _ = patched
    monkeypatch.setattr(store_mod, "list_workflow_instances", lambda limit=1000: [_mk(status="deleted")])
    result = health_check.handle(_mk(id="hc-own", config={}))
    assert result["workflows"][0]["issue"] == "bad status: 'deleted'"


def test_never_run_flagged(monkeypatch, patched):
    store_mod, _ = patched
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    monkeypatch.setattr(store_mod, "list_workflow_instances", lambda limit=1000: [
        _mk(last_run_at=None, next_run_at=past)
    ])
    result = health_check.handle(_mk(id="hc-own", config={}))
    assert result["workflows"][0]["issue"] == "never run"


def test_last_run_failed_via_success_false(monkeypatch, patched):
    store_mod, _ = patched
    monkeypatch.setattr(store_mod, "list_workflow_instances", lambda limit=1000: [
        _mk(last_result={"success": False, "error": "boom"})
    ])
    result = health_check.handle(_mk(id="hc-own", config={}))
    entry = result["workflows"][0]
    assert entry["issue"] == "last run failed"
    assert entry["last_error"] == "boom"


def test_last_run_failed_via_bare_error_string(monkeypatch, patched):
    store_mod, _ = patched
    monkeypatch.setattr(store_mod, "list_workflow_instances", lambda limit=1000: [
        _mk(last_result={"error": "kaboom"})
    ])
    result = health_check.handle(_mk(id="hc-own", config={}))
    assert result["workflows"][0]["issue"] == "last run failed"


def test_overdue_flagged_only_when_active(monkeypatch, patched):
    store_mod, _ = patched
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    monkeypatch.setattr(store_mod, "list_workflow_instances", lambda limit=1000: [
        _mk(next_run_at=past, status="active"),
        _mk(id="wf-2", next_run_at=past, status="paused"),
    ])
    result = health_check.handle(_mk(id="hc-own", config={}))
    issues = {w["id"]: w["issue"] for w in result["workflows"]}
    assert issues["wf-1"] == "overdue"
    assert "wf-2" not in issues  # paused + overdue next_run_at is not flagged


def test_target_single_workflow_by_id(monkeypatch, patched):
    store_mod, _ = patched
    monkeypatch.setattr(store_mod, "get_workflow_instance", lambda wid: _mk(id=wid) if wid == "wf-1" else None)
    result = health_check.handle(_mk(id="hc-own", config={"workflow_id": "wf-1"}))
    assert result["checked"] == 1


def test_target_missing_workflow_id_reports_none_found(monkeypatch, patched):
    store_mod, _ = patched
    monkeypatch.setattr(store_mod, "get_workflow_instance", lambda wid: None)
    result = health_check.handle(_mk(config={"workflow_id": "missing"}))
    assert result["checked"] == 0


def test_success_false_when_problems_exist(monkeypatch, patched):
    store_mod, _ = patched
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    monkeypatch.setattr(store_mod, "list_workflow_instances", lambda limit=1000: [
        _mk(last_run_at=None, next_run_at=past)
    ])
    result = health_check.handle(_mk(id="hc-own", config={}))
    assert result["success"] is False
    assert result["error"] is not None


def test_telegram_delivery_sends_summary(monkeypatch, patched):
    store_mod, _ = patched
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    monkeypatch.setattr(store_mod, "list_workflow_instances", lambda limit=1000: [
        _mk(last_run_at=None, next_run_at=past)
    ])

    result = health_check.handle(_mk(id="hc-own", action_type="telegram", config={}))
    assert result["telegram_message"] is not None
    assert "problem" in result["telegram_message"]


def test_telegram_delivery_failure_does_not_raise(monkeypatch, patched):
    store_mod, _ = patched
    monkeypatch.setattr(store_mod, "list_workflow_instances", lambda limit=1000: [_mk()])

    def boom(msg):
        raise RuntimeError("network down")

    import tools.telegram_send as real_telegram
    monkeypatch.setattr(real_telegram, "send_telegram", boom)

    # must not raise even though telegram send fails
    result = health_check.handle(_mk(id="hc-own", action_type="telegram", config={"include_ok": True}))
    assert result["success"] is True


def test_skips_own_instance(monkeypatch, patched):
    """Health check should not flag itself even if it would be a problem."""
    store_mod, _ = patched
    own_id = "health-check-1"
    own = _mk(id=own_id, last_run_at=None)  # never run — normally a problem
    monkeypatch.setattr(store_mod, "list_workflow_instances", lambda limit=1000: [own])
    result = health_check.handle(_mk(id=own_id, config={}))
    assert result["checked"] == 0  # skipped the only instance (itself)
    assert result["problems"] == 0
    assert result["success"] is True
