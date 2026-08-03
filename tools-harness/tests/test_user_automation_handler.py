"""One-test-per-uncovered dispatch branch for user_automation handler.

Existing coverage from sibling test files:
  test_user_automation_workflow.py          -> _interp, workflow e2e
  test_user_automation_branching.py         -> condition/goto branching
  test_user_automation_branching_edge_cases.py -> edge-case branching
  test_user_automation_dedupe.py            -> telegram query + pubmed dedup

This file covers the remaining 6 top-level action_type branches + 1 edge case.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.telegram_send as tg_mod
import tools.imessage_search as im_mod
import tools.registry as reg_mod
import store.workflow_store as wf_store
from jobs.handlers.user_automation import handle


def test_notification_action_dispatches_and_stores(monkeypatch):
    msg = []
    monkeypatch.setattr(wf_store, "add_notification",
                        lambda message, source, level: msg.append(message))
    result = handle({"action_type": "notification", "config": {"message": "hi"},
                     "task_name": "test"})
    assert result == {"success": True}
    assert msg == ["hi"]


def test_notification_action_default_message(monkeypatch):
    msg = []
    monkeypatch.setattr(wf_store, "add_notification",
                        lambda message, source, level: msg.append(message))
    result = handle({"action_type": "notification", "config": {},
                     "task_name": "custom_notif"})
    assert result == {"success": True}
    assert "custom_notif" in msg[0]


def test_imessage_action_simple_send(monkeypatch):
    sent = []
    monkeypatch.setattr(im_mod, "send",
                        lambda to, message: sent.append((to, message)) or "ok")
    result = handle({
        "action_type": "imessage", "config": {"recipient": "+1555", "message": "hi"},
        "task_name": "test",
    })
    assert result["success"] is True
    assert sent == [("+1555", "hi")]


def test_imessage_action_missing_recipient_returns_error(monkeypatch):
    monkeypatch.setattr(im_mod, "send", lambda to, message: "ok")
    monkeypatch.delenv("IMESSAGE_DEFAULT_TARGET", raising=False)
    result = handle({
        "action_type": "imessage", "config": {"message": "hi"},
        "task_name": "test",
    })
    assert result["success"] is False
    assert "recipient" in result["error"]


def test_http_webhook_action_dispatches(monkeypatch):
    import requests as req_mod
    calls = []
    monkeypatch.setattr(req_mod, "post",
                        lambda url, json, timeout: calls.append((url, json)) or type("R", (), {"ok": True, "status_code": 200})())
    result = handle({
        "action_type": "http_webhook",
        "config": {"url": "https://hook.example.com/notify", "payload": {"key": "val"}},
        "task_name": "test",
    })
    assert result["success"] is True
    assert calls == [("https://hook.example.com/notify", {"key": "val"})]


def test_http_webhook_missing_url_returns_error():
    result = handle({
        "action_type": "http_webhook", "config": {},
        "task_name": "test",
    })
    assert result["success"] is False
    assert "url" in result["error"]


def test_telegram_simple_send(monkeypatch):
    sent = []
    monkeypatch.setattr(tg_mod, "send_telegram",
                        lambda m: sent.append(m) or "ok")
    result = handle({
        "action_type": "telegram", "config": {"message": "hello world"},
        "task_name": "test",
    })
    assert result["success"] is True
    assert sent == ["hello world"]


def test_tool_call_action_success(monkeypatch):
    monkeypatch.setitem(reg_mod.TOOL_TAGS, "browser_test",
                        frozenset(["browser"]))
    monkeypatch.setattr(reg_mod, "execute_tool",
                        lambda name, args: "result ok")
    monkeypatch.setattr(reg_mod, "is_error_result",
                        lambda r: False)
    monkeypatch.setattr(tg_mod, "send_telegram",
                        lambda m: "ok")
    result = handle({
        "action_type": "tool_call",
        "config": {"tool_name": "browser_test", "tool_args": {"url": "http://x.com"}},
        "task_name": "test",
    })
    assert result["success"] is True
    assert result["detail"] == "result ok"


def test_tool_call_non_browser_tool_returns_error():
    result = handle({
        "action_type": "tool_call",
        "config": {"tool_name": "nonexistent_tool"},
        "task_name": "test",
    })
    assert result["success"] is False
    assert "browser-tagged" in result["error"]


def test_email_simple_send(monkeypatch):
    sent = []
    monkeypatch.setitem(reg_mod.EXECUTORS, "send_email",
                        lambda args: sent.append(args) or "ok")
    result = handle({
        "action_type": "email",
        "config": {"recipient": "a@b.com", "subject": "sub", "message": "body text"},
        "task_name": "test",
    })
    assert result["success"] is True
    assert len(sent) == 1
    assert sent[0]["to"] == "a@b.com"
    assert sent[0]["subject"] == "sub"
    assert sent[0]["body"] == "body text"


def test_email_missing_recipient_returns_error():
    result = handle({
        "action_type": "email", "config": {},
        "task_name": "test",
    })
    assert result["success"] is False
    assert "recipient" in result["error"]


def test_unknown_action_type_returns_error():
    result = handle({
        "action_type": "made_up_action",
        "config": {}, "task_name": "test",
    })
    assert result["success"] is False
    assert "unknown action_type" in result["error"]


if __name__ == "__main__":
    test_notification_action_dispatches_and_stores(None)
    test_notification_action_default_message(None)
    test_imessage_action_simple_send(None)
    test_imessage_action_missing_recipient_returns_error(None)
    test_http_webhook_action_dispatches(None)
    test_http_webhook_missing_url_returns_error()
    test_telegram_simple_send(None)
    test_tool_call_action_success(None)
    test_tool_call_non_browser_tool_returns_error()
    test_email_simple_send(None)
    test_email_missing_recipient_returns_error()
    test_unknown_action_type_returns_error()
    print("all handler dispatch tests passed")
