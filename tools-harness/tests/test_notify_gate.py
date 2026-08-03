"""decide_and_notify: LLM gets a second look at a handler's own alert
verdict — suppress low-signal, notify on high-signal, and never go silent
(fall back to the handler's fallback_alert) if the decision call itself fails.
"""
import json

import pytest

from jobs import notify_gate
from clients.cost_guard import BudgetExceededError


@pytest.fixture
def notified(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "store.workflow_store.add_notification",
        lambda **kw: calls.append(kw),
    )
    monkeypatch.setattr(notify_gate, "_push_telegram", lambda text: None)
    return calls


def test_low_signal_suppressed(notified, monkeypatch):
    monkeypatch.setattr("clients.cloud_client.chat", lambda **kw: json.dumps({"alert": False, "reason": "not actionable"}))
    sent = notify_gate.decide_and_notify(finding="minor thing", source="test", fallback_alert=True)
    assert sent is False
    assert notified == []


def test_high_signal_notifies(notified, monkeypatch):
    monkeypatch.setattr("clients.cloud_client.chat", lambda **kw: json.dumps({"alert": True, "reason": "big opportunity"}))
    sent = notify_gate.decide_and_notify(finding="huge thing", source="test", fallback_alert=False)
    assert sent is True
    assert len(notified) == 1
    assert "big opportunity" in notified[0]["message"]


def test_budget_exceeded_falls_back_to_fallback_alert(notified, monkeypatch):
    def boom(**kw):
        raise BudgetExceededError("over budget")
    monkeypatch.setattr("clients.cloud_client.chat", boom)

    sent_true = notify_gate.decide_and_notify(finding="x", source="test", fallback_alert=True)
    assert sent_true is True
    assert len(notified) == 1

    notified.clear()
    sent_false = notify_gate.decide_and_notify(finding="x", source="test", fallback_alert=False)
    assert sent_false is False
    assert notified == []


def test_wake_agent_uses_verified_answer(notified, monkeypatch):
    monkeypatch.setattr("clients.cloud_client.chat", lambda **kw: json.dumps({"alert": True, "reason": "big opportunity"}))
    monkeypatch.setattr(
        notify_gate, "_verify_via_agent",
        lambda finding, source: "Verified take: checks out, worth a look.",
    )
    sent = notify_gate.decide_and_notify(finding="huge thing", source="test", fallback_alert=False, wake_agent=True)
    assert sent is True
    assert notified[0]["message"] == "Verified take: checks out, worth a look."


def test_wake_agent_falls_back_to_raw_finding_on_verify_failure(notified, monkeypatch):
    monkeypatch.setattr("clients.cloud_client.chat", lambda **kw: json.dumps({"alert": True, "reason": "big opportunity"}))
    monkeypatch.setattr(notify_gate, "_verify_via_agent", lambda finding, source: "")
    sent = notify_gate.decide_and_notify(finding="huge thing", source="test", fallback_alert=False, wake_agent=True)
    assert sent is True
    assert "big opportunity" in notified[0]["message"]


def test_call_phone_true_places_call(notified, monkeypatch):
    monkeypatch.setattr("clients.cloud_client.chat", lambda **kw: json.dumps({"alert": True, "reason": "urgent"}))
    calls = []
    monkeypatch.setattr(notify_gate, "_call_linphone", lambda text: calls.append(text))
    sent = notify_gate.decide_and_notify(finding="huge thing", source="test", fallback_alert=False, call_phone=True)
    assert sent is True
    assert len(calls) == 1


def test_bypass_gate_skips_llm_and_uses_fallback(notified, monkeypatch):
    def boom(**kw):
        raise AssertionError("chat() should not be called when bypass_gate=True")
    monkeypatch.setattr("clients.cloud_client.chat", boom)
    sent = notify_gate.decide_and_notify(finding="strong finding", source="test", fallback_alert=True, bypass_gate=True)
    assert sent is True
    assert notified[0]["message"] == "strong finding"


def test_bypass_gate_false_fallback_suppresses(notified, monkeypatch):
    def boom(**kw):
        raise AssertionError("chat() should not be called when bypass_gate=True")
    monkeypatch.setattr("clients.cloud_client.chat", boom)
    sent = notify_gate.decide_and_notify(finding="weak finding", source="test", fallback_alert=False, bypass_gate=True)
    assert sent is False
    assert notified == []


def test_call_phone_false_never_calls(notified, monkeypatch):
    monkeypatch.setattr("clients.cloud_client.chat", lambda **kw: json.dumps({"alert": True, "reason": "urgent"}))
    calls = []
    monkeypatch.setattr(notify_gate, "_call_linphone", lambda text: calls.append(text))
    notify_gate.decide_and_notify(finding="huge thing", source="test", fallback_alert=False)
    assert calls == []


def test_call_phone_failure_does_not_block_notification(notified, monkeypatch):
    monkeypatch.setattr("clients.cloud_client.chat", lambda **kw: json.dumps({"alert": True, "reason": "urgent"}))

    def boom(**kw):
        raise RuntimeError("no ringback module")
    monkeypatch.setattr("tools.connector_ringback.call_my_phone", lambda message: (_ for _ in ()).throw(RuntimeError("docker down")))
    sent = notify_gate.decide_and_notify(finding="huge thing", source="test", fallback_alert=False, call_phone=True)
    assert sent is True
    assert len(notified) == 1
