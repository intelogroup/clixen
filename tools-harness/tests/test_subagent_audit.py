"""subagent_audit: audit LLM gets a look at what a subagent buried
(IGNORE'd items, suppressed alerts) and can auto_tune/auto_rewrite/escalate —
but a low-confidence verdict is always forced to escalate instead of acting,
and a failed audit call must escalate too, never go silent.
"""
import json

import pytest

from jobs.handlers import subagent_audit
from store import reddit_intel_store as store


@pytest.fixture
def notified(monkeypatch):
    calls = []
    monkeypatch.setattr("store.workflow_store.add_notification", lambda **kw: calls.append(kw))
    return calls


@pytest.fixture
def seeded(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "reddit_intel_audit_test.db")
    store._schema_ready_paths.clear()
    store.log_suppressed_alert("reddit_intel", "minor thing", "not actionable")
    store.link("t3_abc", "insight1", "IGNORE")
    return store


def test_high_confidence_auto_tune(notified, seeded, monkeypatch):
    verdict = {
        "diagnosis": "near-dup threshold too tight, missing real dupes",
        "confidence": 0.9, "action": "auto_tune",
        "threshold_changes": {"near_dup_max_distance": 0.8},
    }
    monkeypatch.setattr("clients.cloud_client.chat", lambda **kw: json.dumps(verdict))

    result = subagent_audit.audit_one("reddit_intel")

    assert result["action"] == "auto_tune"
    assert store.get_config("near_dup_max_distance", None) == 0.8
    assert len(notified) == 1
    assert "0.65 -> 0.8" in notified[0]["message"]


def test_high_confidence_auto_rewrite_prompt_still_escalates(notified, seeded, monkeypatch):
    # auto_rewrite_prompt always escalates now, regardless of confidence — a
    # subagent must never rewrite its own prompt unsupervised.
    verdict = {
        "diagnosis": "merge prompt too lenient on IGNORE",
        "confidence": 0.85, "action": "auto_rewrite_prompt",
        "prompt_name": "merge_decision", "new_prompt": "Be stricter about IGNORE.",
    }
    monkeypatch.setattr("clients.cloud_client.chat", lambda **kw: json.dumps(verdict))

    result = subagent_audit.audit_one("reddit_intel")

    assert result["action"] == "escalate"
    assert store.get_prompt("merge_decision", "") != "Be stricter about IGNORE."
    assert len(notified) == 1


def test_low_confidence_forces_escalate(notified, seeded, monkeypatch):
    verdict = {
        "diagnosis": "maybe missing signal, not sure",
        "confidence": 0.4, "action": "auto_tune",
        "threshold_changes": {"near_dup_max_distance": 0.9},
    }
    monkeypatch.setattr("clients.cloud_client.chat", lambda **kw: json.dumps(verdict))

    result = subagent_audit.audit_one("reddit_intel")

    assert result["action"] == "escalate"
    assert store.get_config("near_dup_max_distance", None) is None  # never applied
    assert len(notified) == 1
    assert notified[0]["level"] == "warning"


def test_audit_call_failure_escalates(notified, seeded, monkeypatch):
    def boom(**kw):
        raise ValueError("malformed")
    monkeypatch.setattr("clients.cloud_client.chat", boom)

    result = subagent_audit.audit_one("reddit_intel")

    assert result["action"] == "escalate"
    assert len(notified) == 1
    assert notified[0]["level"] == "warning"


def test_nothing_to_review_is_noop(notified, monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "reddit_intel_audit_empty.db")
    store._schema_ready_paths.clear()

    result = subagent_audit.audit_one("reddit_intel")

    assert result["action"] == "none"
    assert notified == []


def test_rollback_prompt_restores_prior_version(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "reddit_intel_rollback_test.db")
    store._schema_ready_paths.clear()

    v1 = store.set_prompt("merge_decision", "original prompt", created_by="human")
    store.set_prompt("merge_decision", "auto-rewritten prompt", created_by="auto-audit")
    assert store.get_prompt("merge_decision", "") == "auto-rewritten prompt"

    assert store.rollback_prompt("merge_decision", v1) is True
    assert store.get_prompt("merge_decision", "") == "original prompt"
