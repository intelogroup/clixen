"""Tests for store/run_store.py — durable run journal (M1).

Isolated per-test DB (autouse fixture patches _DB_PATH to tmp_path), so the
live data/run_store.sqlite is never touched.
"""
from __future__ import annotations

import threading

import pytest

from store import run_store as rs


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "_DB_PATH", tmp_path / "run_store.sqlite")
    # Don't touch the real Keychain in tests unless a test opts in.
    monkeypatch.setattr(rs, "_vault_secret_values", lambda: frozenset())
    yield


# ── Journal basics ────────────────────────────────────────────────────────

def test_create_run_and_anchor_event():
    rid = rs.create_run("monitor pubmed nightly", policy={"deadline_s": 3600})
    run = rs.get_run(rid)
    assert run["status"] == "queued"
    assert run["goal"] == "monitor pubmed nightly"
    assert run["policy"] == {"deadline_s": 3600}
    assert run["trigger"] == "chat-send"
    assert run["last_seq"] == 1  # anchor `run` event
    events = rs.get_events(rid)
    assert len(events) == 1 and events[0]["kind"] == "run"


def test_append_and_replay_sequence():
    rid = rs.create_run("g")
    seqs = [rs.append_event(rid, "user_msg", {"text": "hi"}),
            rs.append_event(rid, "assistant_msg", {"text": "hello"}),
            rs.append_event(rid, "tool_call", {"tool": "browser_open", "id": "t1"})]
    assert seqs == [2, 3, 4]
    # Incremental replay: after_seq semantics (the loop's resume cursor)
    assert [e["seq"] for e in rs.get_events(rid, after_seq=3)] == [4]


def test_event_kind_whitelist_and_unknown_run():
    rid = rs.create_run("g")
    with pytest.raises(ValueError, match="unknown event kind"):
        rs.append_event(rid, "made_up_kind", {})
    with pytest.raises(KeyError):
        rs.append_event("nope", "user_msg", {})


def test_trigger_validation():
    with pytest.raises(ValueError, match="unknown trigger"):
        rs.create_run("g", trigger="cron-hack")


def test_seq_unique_under_concurrent_appends():
    rid = rs.create_run("g")
    errors: list[Exception] = []

    def writer(n):
        try:
            for i in range(10):
                rs.append_event(rid, "heartbeat", {"n": n, "i": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    seqs = [e["seq"] for e in rs.get_events(rid)]
    assert seqs == sorted(set(seqs)) and len(seqs) == 41  # anchor + 40


# ── Redaction (write boundary) ────────────────────────────────────────────

def test_scrub_keyname_layer():
    out, redacted = rs.scrub(
        {"username": "jim", "password": "hunter2", "api_key": "sk-123",
         "nested": {"Authorization": "Bearer xyz", "ok": 1}},
        secrets=frozenset(),
    )
    assert out["password"] == "[REDACTED:password]"
    assert out["api_key"] == "[REDACTED:api_key]"
    assert out["nested"]["Authorization"] == "[REDACTED:Authorization]"
    assert out["nested"]["ok"] == 1 and out["username"] == "jim"
    assert "password" in redacted and "nested.Authorization" in redacted


def test_scrub_vault_value_layer():
    secret = "S3cret-Pa55!"
    out, redacted = rs.scrub(
        {"text": f"login with {secret} now", "plain": "unrelated"},
        secrets=frozenset({secret}),
    )
    assert secret not in out["text"]
    assert "[REDACTED:vault]" in out["text"]
    assert out["plain"] == "unrelated"
    assert any("vault" in r for r in redacted)


def test_append_event_scrubs_and_marks():
    monkey_secrets = frozenset({"vault-pw-123"})

    def fake_vault():
        return monkey_secrets

    import store.run_store as mod
    # re-patch (autouse set the no-op) for this test only
    orig = mod._vault_secret_values
    mod._vault_secret_values = fake_vault
    try:
        rid = rs.create_run("g")
        rs.append_event(rid, "tool_result",
                        {"password": "inline-pw", "out": "used vault-pw-123 ok"})
        ev = rs.get_events(rid, after_seq=1)[0]
        blob = str(ev["payload"])
        assert "inline-pw" not in blob and "vault-pw-123" not in blob
        assert "[REDACTED:password]" in blob and "[REDACTED:vault]" in blob
        assert "_redacted" in ev["payload"]
    finally:
        mod._vault_secret_values = orig


# ── Round-boundary snapshots (WS1 issue 1) ────────────────────────────────

def test_append_round_snapshot_and_latest_round():
    rid = rs.create_run("g")
    rs.append_event(rid, "assistant_msg", {"text": "a"})
    seq1 = rs.append_round_snapshot(rid, round_idx=0, model="deepseek/deepseek-v4-flash",
                                    escalated=False, consecutive_errors=0,
                                    force_tool_consumed=True)
    rs.append_event(rid, "assistant_msg", {"text": "b"})
    seq2 = rs.append_round_snapshot(rid, round_idx=1, model="openai/gpt-4.1-mini",
                                    escalated=True, consecutive_errors=3,
                                    force_tool_consumed=False)
    assert seq2 == seq1 + 2  # snapshot seq, then next event
    snap = rs.latest_round(rid)
    assert snap == {"round_idx": 1, "model": "openai/gpt-4.1-mini",
                    "escalated": True, "consecutive_errors": 3,
                    "force_tool_consumed": False, "seq": seq2}
    kinds = [e["kind"] for e in rs.get_events(rid)]
    assert kinds.count("round") == 2


def test_latest_round_none_for_fresh_run():
    assert rs.latest_round(rs.create_run("g")) is None


def test_round_snapshot_scrubs_secrets():
    rid = rs.create_run("g")
    rs.append_round_snapshot(rid, round_idx=0, model="m", extra={"token": "sk-abc-123"})
    ev = [e for e in rs.get_events(rid) if e["kind"] == "round"][0]
    assert ev["payload"]["extra"]["token"] == "[REDACTED:token]"


# ── Control intents (M2: pause / resume / kill / steer) ──────────────────

def test_append_control_records_intent_without_status_change():
    rid = rs.create_run("g")
    rs.set_status(rid, "running")
    seq = rs.append_control(rid, "pause")
    ev = rs.get_events(rid)[-1]
    assert ev["seq"] == seq and ev["kind"] == "control"
    assert ev["payload"] == {"action": "pause"}
    assert rs.get_run(rid)["status"] == "running", "the loop owns transitions, not the requester"


def test_append_control_steer_carries_text():
    rid = rs.create_run("g")
    rs.append_control(rid, "steer", text="focus on pricing first")
    ev = rs.get_events(rid)[-1]
    assert ev["payload"] == {"action": "steer", "text": "focus on pricing first"}


def test_append_control_rejects_unknown_action():
    rid = rs.create_run("g")
    with pytest.raises(ValueError, match="unknown control action"):
        rs.append_control(rid, "explode")


def test_pending_controls_since_cursor():
    rid = rs.create_run("g")
    s1 = rs.append_control(rid, "pause")
    s2 = rs.append_control(rid, "steer", text="x")
    pending = rs.pending_controls(rid, after_seq=0)
    assert [p["seq"] for p in pending] == [s1, s2]
    assert [p["action"] for p in pending] == ["pause", "steer"]
    assert rs.pending_controls(rid, after_seq=s1) == [
        {"seq": s2, "action": "steer", "text": "x"}]


# ── Status machine ────────────────────────────────────────────────────────

def test_happy_path_transitions_and_status_events():
    rid = rs.create_run("g")
    assert rs.set_status(rid, "running") == "running"
    assert rs.set_status(rid, "paused") == "paused"
    assert rs.set_status(rid, "running") == "running"
    assert rs.set_status(rid, "succeeded") == "succeeded"
    kinds = [e["kind"] for e in rs.get_events(rid)]
    assert kinds.count("status") == 4
    # final status event records the transition source
    last_status = [e for e in rs.get_events(rid) if e["kind"] == "status"][-1]
    assert last_status["payload"] == {"status": "succeeded", "from": "running"}


def test_invalid_transitions_rejected():
    rid = rs.create_run("g")  # queued
    with pytest.raises(ValueError, match="invalid transition"):
        rs.set_status(rid, "succeeded")  # queued -> succeeded not allowed
    with pytest.raises(ValueError, match="unknown status"):
        rs.set_status(rid, "exploded")
    rs.set_status(rid, "running")
    rs.set_status(rid, "failed")
    # failed IS resumable per the plan
    assert rs.set_status(rid, "running") == "running"
    rs.set_status(rid, "succeeded")
    with pytest.raises(ValueError, match="invalid transition"):
        rs.set_status(rid, "running")  # succeeded is terminal — rerun = new run


def test_resumable_flag_matches_plan():
    assert rs.get_run(rs.create_run("g"))["resumable"] is False  # queued
    rid = rs.create_run("g")
    rs.set_status(rid, "running"); rs.set_status(rid, "killed")
    assert rs.get_run(rid)["resumable"] is True
    rid = rs.create_run("g")
    rs.set_status(rid, "running"); rs.set_status(rid, "budget-exceeded")
    assert rs.get_run(rid)["resumable"] is True
    rid = rs.create_run("g")
    rs.set_status(rid, "running"); rs.set_status(rid, "succeeded")
    assert rs.get_run(rid)["resumable"] is False


def test_record_attempt_counts_and_retries():
    rid = rs.create_run("g")
    rs.set_status(rid, "running")
    assert rs.record_attempt(rid) == 1
    assert rs.get_run(rid)["status"] == "retrying"
    assert rs.record_attempt(rid) == 2
    kinds = [e["kind"] for e in rs.get_events(rid)]
    assert kinds.count("heartbeat") == 2
    assert rs.set_status(rid, "running") == "running"


def test_list_runs_filter_and_order():
    a = rs.create_run("first")
    b = rs.create_run("second")
    rs.set_status(b, "running")
    running = rs.list_runs(status="running")
    assert [r["run_id"] for r in running] == [b]
    all_runs = rs.list_runs()
    assert {r["run_id"] for r in all_runs} >= {a, b}
