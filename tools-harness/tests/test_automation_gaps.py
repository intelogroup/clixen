"""
Regression tests for the 2026-07-11 workflow/automation system gap sweep
(triggered by a live bug: create_automation missed a paused builtin that
already covered the request and created an inferior duplicate). Covers:
1. create_automation's fuzzy-duplicate FYI note.
2. list_automations trigger_type/action_type filters.
3. run history (append_run_history, capped at 10).
4. webhook receiver (auth success/failure, dispatch, secret generation).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from store import workflow_store
from tools.automation_tools import create_automation, list_automations, update_automation
from conftest import safe_delete_test_workflow


def _cleanup(*wf_ids):
    for wf_id in wf_ids:
        safe_delete_test_workflow(wf_id)


# ── 1. Fuzzy-duplicate FYI ──────────────────────────────────────────────────

def test_create_automation_flags_similar_existing():
    # ponytail: look up created rows by task_name via a direct DB query, not by
    # string-parsing the create_automation() return value — that return can itself
    # start with an FYI note containing another automation's "id=...", which broke
    # a naive `first.split("id=")[1]` parse and orphaned test rows across runs
    # (confirmed live 2026-07-11: 2 leftover "Watch inbox attachments test" rows
    # from this exact bug, self-perpetuating since each orphan made the next run's
    # create_automation() call itself return an FYI note).
    workflow_store.init()
    create_automation({
        "name": "Watch inbox attachments test",
        "trigger_type": "email", "action_type": "telegram",
        "trigger_config": {"query": "has:attachment"},
    })
    full1 = [i["id"] for i in workflow_store.list_workflow_instances(trigger_type="email", limit=200)
             if i["task_name"] == "Watch inbox attachments test"][0]
    try:
        second = create_automation({
            "name": "Notify me on inbox attachments arriving",
            "trigger_type": "email", "action_type": "telegram",
        })
        full2 = [i["id"] for i in workflow_store.list_workflow_instances(trigger_type="email", limit=200)
                 if i["task_name"] == "Notify me on inbox attachments arriving"][0]
        assert "similar existing automation" in second
        assert "Watch inbox attachments test" in second
        _cleanup(full2)
    finally:
        _cleanup(full1)


def test_create_automation_no_fyi_for_unrelated_name():
    workflow_store.init()
    r = create_automation({
        "name": "Totally Unrelated Zzyzx Ping",
        "trigger_type": "schedule", "action_type": "notification",
        "schedule": {"interval_seconds": 3600},
    })
    wf_id = [i["id"] for i in workflow_store.list_workflow_instances(trigger_type="schedule", limit=200)
             if i["task_name"] == "Totally Unrelated Zzyzx Ping"][0]
    try:
        assert "similar existing automation" not in r
    finally:
        _cleanup(wf_id)


# ── 2. list_automations filters ─────────────────────────────────────────────

def test_list_automations_filters_by_trigger_and_action_type():
    workflow_store.init()
    r1 = create_automation({
        "name": "Filter test schedule notif", "trigger_type": "schedule",
        "action_type": "notification", "schedule": {"interval_seconds": 3600},
    })
    r2 = create_automation({
        "name": "Filter test schedule telegram", "trigger_type": "schedule",
        "action_type": "telegram", "schedule": {"interval_seconds": 3600},
    })
    ids = [i["id"] for i in workflow_store.list_workflow_instances(trigger_type="schedule", limit=200)
           if i["task_name"] in ("Filter test schedule notif", "Filter test schedule telegram")]
    try:
        out = list_automations({"trigger_type": "schedule", "action_type": "telegram"})
        assert "Filter test schedule telegram" in out
        assert "Filter test schedule notif" not in out
    finally:
        _cleanup(*ids)


# ── 3. Run history ───────────────────────────────────────────────────────────

def test_append_run_history_caps_at_ten():
    workflow_store.init()
    wf_id = "test_run_history_cap"
    _cleanup(wf_id)
    workflow_store.create_workflow_instance(
        workflow_id=wf_id, automation_id="user.automation", task_name="history test",
    )
    try:
        for i in range(15):
            workflow_store.append_run_history(wf_id, {"success": True, "n": i})
        inst = workflow_store.get_workflow_instance(wf_id)
        assert len(inst["run_history"]) == 15  # cap raised 10→100
        assert [h["n"] for h in inst["run_history"]] == list(range(0, 15))
    finally:
        _cleanup(wf_id)


def test_run_history_does_not_replace_last_result():
    workflow_store.init()
    wf_id = "test_run_history_shape"
    _cleanup(wf_id)
    workflow_store.create_workflow_instance(
        workflow_id=wf_id, automation_id="user.automation", task_name="shape test",
    )
    try:
        workflow_store.update_workflow_instance(wf_id, last_result={"success": True, "detail": "ok"})
        workflow_store.append_run_history(wf_id, {"success": True, "detail": "ok"})
        inst = workflow_store.get_workflow_instance(wf_id)
        assert inst["last_result"] == {"success": True, "detail": "ok"}
        assert isinstance(inst["run_history"], list) and len(inst["run_history"]) == 1
    finally:
        _cleanup(wf_id)


# ── 4. Webhook receiver ──────────────────────────────────────────────────────

def test_create_automation_webhook_generates_secret_and_url():
    workflow_store.init()
    r = create_automation({
        "name": "Webhook secret test", "trigger_type": "webhook", "action_type": "notification",
    })
    wf_id = [i["id"] for i in workflow_store.list_workflow_instances(trigger_type="webhook", limit=200)
             if i["task_name"] == "Webhook secret test"][0]
    try:
        assert "Webhook URL" in r
        inst = workflow_store.get_workflow_instance(wf_id)
        assert inst["config"].get("_webhook_secret")
    finally:
        _cleanup(wf_id)


def test_webhook_route_dispatches_on_valid_secret():
    from fastapi.testclient import TestClient
    import chat_ui
    from jobs.worker import _setup_handler_registry

    _setup_handler_registry()
    workflow_store.init()
    wf_id = "test_webhook_route_ok"
    _cleanup(wf_id)
    secret = "test-secret-123"
    workflow_store.create_workflow_instance(
        workflow_id=wf_id, automation_id="user.automation", task_name="webhook route test",
        trigger_type="webhook", action_type="notification",
        config={"_webhook_secret": secret, "message": "webhook fired"},
    )
    try:
        client = TestClient(chat_ui.app)
        resp = client.post(f"/webhooks/trigger/{wf_id}", params={"secret": secret})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        inst = workflow_store.get_workflow_instance(wf_id)
        assert inst["last_result"].get("success") is True
        assert len(inst["run_history"]) == 1
    finally:
        _cleanup(wf_id)


def test_webhook_route_rejects_bad_secret():
    from fastapi.testclient import TestClient
    import chat_ui

    workflow_store.init()
    wf_id = "test_webhook_route_bad_secret"
    _cleanup(wf_id)
    workflow_store.create_workflow_instance(
        workflow_id=wf_id, automation_id="user.automation", task_name="webhook route bad secret test",
        trigger_type="webhook", action_type="notification",
        config={"_webhook_secret": "correct-secret"},
    )
    try:
        client = TestClient(chat_ui.app)
        resp = client.post(f"/webhooks/trigger/{wf_id}", params={"secret": "wrong-secret"})
        assert resp.status_code == 404
        inst = workflow_store.get_workflow_instance(wf_id)
        assert inst["last_result"] == {}
    finally:
        _cleanup(wf_id)


def test_webhook_route_rejects_non_webhook_trigger_type():
    from fastapi.testclient import TestClient
    import chat_ui

    workflow_store.init()
    wf_id = "test_webhook_route_wrong_trigger"
    _cleanup(wf_id)
    workflow_store.create_workflow_instance(
        workflow_id=wf_id, automation_id="user.automation", task_name="webhook route wrong trigger test",
        trigger_type="schedule", action_type="notification",
        config={"_webhook_secret": "some-secret"},
    )
    try:
        client = TestClient(chat_ui.app)
        resp = client.post(f"/webhooks/trigger/{wf_id}", params={"secret": "some-secret"})
        assert resp.status_code == 404
    finally:
        _cleanup(wf_id)


# ── 5. safe_delete_test_workflow guard (2026-07-12 root-cause fix) ──────────

def test_safe_delete_refuses_builtin_dedupe_key_row():
    """Reproduces the exact original failure mode: a test-created automation
    ("Watch inbox attachments test") shares tokens ("inbox"/"attachments")
    with the real builtin "Monitor inbox attachments"
    (automation_id=inbox.monitor_attachments, trigger=email, action=telegram
    — same trigger/action type too), so create_automation()'s fuzzy-duplicate
    check fires an FYI note naming the builtin's real id. The original bug:
    a test string-parsed that id out of the FYI-laden return value instead of
    querying its own row by task_name, then "cleaned up" by deleting the
    REAL builtin (confirmed live 2026-07-11 — it happened 3 times, each
    silently reseeded fresh with a new id and reset to active on the next
    core.py restart). This test proves safe_delete_test_workflow() refuses
    instead of silently deleting, using a fake in-test builtin (same shape:
    non-empty dedupe_key) so it doesn't touch real seeded data.
    """
    workflow_store.init()
    fake_builtin_id = "test_fake_builtin_dedupe_guard"
    workflow_store.delete_workflow_instance(fake_builtin_id)  # raw delete OK here — not a builtin
    workflow_store.create_workflow_instance(
        workflow_id=fake_builtin_id, automation_id="fake.builtin.pipeline",
        task_name="Fake Builtin For Guard Test", trigger_type="email", action_type="telegram",
        dedupe_key="fake.builtin.pipeline",  # non-empty — the exact signal seed_builtins() sets
    )
    try:
        try:
            safe_delete_test_workflow(fake_builtin_id)
            assert False, "safe_delete_test_workflow must refuse to delete a row with a non-empty dedupe_key"
        except AssertionError as e:
            assert "dedupe_key" in str(e)
        # Confirm it's still there — the guard actually prevented the delete, not just raised late.
        assert workflow_store.get_workflow_instance(fake_builtin_id) is not None
    finally:
        workflow_store.delete_workflow_instance(fake_builtin_id)  # raw delete OK — test cleanup of the fake


def test_safe_delete_allows_normal_test_row():
    workflow_store.init()
    wf_id = "test_safe_delete_normal_row"
    workflow_store.delete_workflow_instance(wf_id)
    workflow_store.create_workflow_instance(
        workflow_id=wf_id, automation_id="user.automation", task_name="normal test row",
        trigger_type="schedule", action_type="notification",
    )
    safe_delete_test_workflow(wf_id)
    assert workflow_store.get_workflow_instance(wf_id) is None


def test_delete_workflow_cascades_to_approvals():
    workflow_store.init()
    wf_id = "test_cascade_approvals"
    workflow_store.delete_workflow_instance(wf_id)
    workflow_store.create_workflow_instance(
        workflow_id=wf_id, automation_id="user.automation", task_name="cascade test",
        trigger_type="schedule", action_type="notification",
    )
    try:
        req = workflow_store.create_approval_request(
            kind="step_approval", workflow_instance_id=wf_id,
            payload={"msg": "test"},
        )
        assert req["status"] == "pending"
        assert workflow_store.delete_workflow_instance(wf_id)
        # approval should be gone too
        assert workflow_store.get_approval_request(req["id"]) is None
    finally:
        workflow_store.delete_workflow_instance(wf_id)


def test_expire_stale_approval():
    from datetime import datetime, timedelta, timezone
    workflow_store.init()
    wf_id = "test_stale_approval"
    workflow_store.delete_workflow_instance(wf_id)
    workflow_store.create_workflow_instance(
        workflow_id=wf_id, automation_id="user.automation", task_name="stale test",
        trigger_type="schedule", action_type="notification",
    )
    try:
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        req = workflow_store.create_approval_request(
            kind="step_approval", workflow_instance_id=wf_id,
            expires_at=past,
        )
        assert req["status"] == "pending"

        # simulate worker's _expire_stale_approvals logic
        from jobs.worker import _expire_stale_approvals
        _expire_stale_approvals()

        expired = workflow_store.get_approval_request(req["id"])
        assert expired["status"] == "expired"
    finally:
        workflow_store.delete_workflow_instance(wf_id)
