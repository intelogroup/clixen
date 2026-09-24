"""Tests for the M3 supervisor: jobs/run_child.py (one run per process) and
jobs/run_supervisor.py (spawn + reap-never-adopt + auto-resume).

Real subprocesses are used; `--fake-model` scripts the rounds so no provider
is called. The child path exercised here is the production one: claim lease →
heartbeat → journal loop → release.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobs import run_supervisor as sup
from store import run_store as rs

HARNESS = str(Path(__file__).resolve().parent.parent)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    db = tmp_path / "sup.sqlite"
    monkeypatch.setenv("CLIXEN_RUN_STORE", str(db))   # child processes inherit it
    monkeypatch.setattr(rs, "_DB_PATH", db)
    monkeypatch.setattr(rs, "_vault_secret_values", lambda: frozenset())
    yield


def _write_script(tmp_path, rounds, marker=None):
    path = tmp_path / "script.json"
    path.write_text(json.dumps({"rounds": rounds, "marker": str(marker or "")}))
    return path


def _spawn(run_id, script, *, lease_ttl=2, extra_env=None):
    env = {**os.environ, "PYTHONPATH": HARNESS, "PYTHONDONTWRITEBYTECODE": "1",
           **(extra_env or {})}
    return subprocess.Popen(
        [sys.executable, "-m", "jobs.run_child", "--run-id", run_id,
         "--fake-model", str(script), "--lease-ttl", str(lease_ttl)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def test_child_completes_a_run_and_releases_its_lease(tmp_path):
    rid = rs.create_run("child run")
    script = _write_script(tmp_path, [{"text": "done", "tool_calls": []}])
    proc = _spawn(rid, script)
    _out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err.decode()[-800:]
    run = rs.get_run(rid)
    assert run["status"] == "succeeded"
    assert run["pid"] is None and run["lease_expires_at"] == 0
    assert run["attempt"] == 1


def test_child_heartbeats_while_running(tmp_path):
    rid = rs.create_run("hb")
    script = _write_script(tmp_path, [
        {"text": "", "tool_calls": [{"id": "t1", "name": "noop", "args": {}}]},
        {"text": "done", "tool_calls": []},
    ], marker=tmp_path / "marker.txt")
    proc = _spawn(rid, script, lease_ttl=1)
    deadline = time.time() + 30
    while time.time() < deadline and rs.get_run(rid)["status"] != "succeeded":
        time.sleep(0.05)
    proc.communicate(timeout=30)
    beats = [e for e in rs.get_events(rid)
             if e["kind"] == "heartbeat" and "lease" in str(e["payload"])]
    assert beats, "child never heartbeated its lease"


def test_supervisor_reaps_dead_child_and_resumes(tmp_path):
    """Reap-never-adopt: a crashed child's lease expires, the supervisor kills
    any orphan pid and spawns a FRESH child that resumes from the journal."""
    rid = rs.create_run("crashy")
    script1 = _write_script(tmp_path, [
        {"text": "", "tool_calls": [{"id": "t1", "name": "noop", "args": {}}]},
        {"text": "unreached", "tool_calls": []},
    ], marker=tmp_path / "m1.txt")
    env = {**os.environ, "PYTHONPATH": HARNESS,
           "CLIXEN_FAKE_CRASH_AFTER_ROUND": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "jobs.run_child", "--run-id", rid,
         "--fake-model", str(script1), "--lease-ttl", "1"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 30
    while time.time() < deadline and rs.get_run(rid)["attempt"] == 0:
        time.sleep(0.05)
    proc.wait(timeout=30)
    assert proc.returncode != 0, "life 1 should have crashed"

    # wait for the crashed child's lease to actually expire before reaping
    deadline = time.time() + 10
    while time.time() < deadline and rs.get_run(rid)["lease_expires_at"] > time.time():
        time.sleep(0.2)

    resumed = sup.reap_orphans(spawn=False)
    assert rid in resumed, resumed
    # reaping releases ownership WITHOUT adopting: the attempt only bumps when
    # a fresh child claims the lease.
    assert rs.get_run(rid)["pid"] is None
    assert rs.get_run(rid)["attempt"] == 1

    script2 = _write_script(tmp_path, [{"text": "final", "tool_calls": []}])
    sup.spawn_run(rid, fake_model=str(script2))
    deadline = time.time() + 40
    while time.time() < deadline and rs.get_run(rid)["status"] != "succeeded":
        time.sleep(0.1)
    assert rs.get_run(rid)["status"] == "succeeded"
    assert rs.get_run(rid)["attempt"] == 2, "resume must not fork extra attempts"


def test_reap_never_adopts_live_child(tmp_path):
    rid = rs.create_run("live")
    script = _write_script(tmp_path, [
        {"text": "", "tool_calls": [{"id": "t1", "name": "noop", "args": {}}]},
        {"text": "done", "tool_calls": []},
    ])
    proc = _spawn(rid, script, lease_ttl=60)  # long lease = healthy
    deadline = time.time() + 30
    while time.time() < deadline and rs.get_run(rid)["attempt"] == 0:
        time.sleep(0.05)
    assert sup.reap_orphans(spawn=False) == [], "a leased child must not be reaped"
    proc.communicate(timeout=30)
    assert proc.returncode == 0


def test_child_marks_the_run_failed_on_crash(tmp_path):
    """A child that dies mid-run must NOT leave a zombie: the run goes
    `failed` (journal preserved, resumable) instead of `running` with no
    lease, which nothing would ever heal."""
    rid = rs.create_run("doomed by provider error")
    # round 1 issues a tool call (denied: empty allowlist), round 2 reaches the
    # crash hook — an unhandled os._exit, i.e. a child that dies mid-run
    script = _write_script(tmp_path, [
        {"text": "", "tool_calls": [{"id": "t1", "name": "noop", "args": {}}]},
        {"text": "never", "tool_calls": []}])
    env = {"CLIXEN_FAKE_RAISE_AFTER_ROUND": "1", "PYTHONPATH": HARNESS}
    proc = subprocess.Popen(
        [sys.executable, "-m", "jobs.run_child", "--run-id", rid,
         "--fake-model", str(script), "--lease-ttl", "30"],
        env={**os.environ, **env}, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    proc.wait(timeout=60)
    assert proc.returncode != 0
    run = rs.get_run(rid)
    assert run["status"] == "failed", run
    assert run["resumable"] is True and run["pid"] is None
    reasons = [e["payload"].get("reason") for e in rs.get_events(rid)
               if e["kind"] == "status"]
    assert any("crash" in (r or "").lower() or "error" in (r or "").lower()
               for r in reasons), reasons


def test_dead_letter_after_repeated_crashes(tmp_path):
    rid = rs.create_run("doomed")
    for _ in range(3):
        rs.claim_lease(rid, pid=os.getpid(), ttl_s=0, max_attempts=3)
    assert rs.get_run(rid)["status"] == "failed"
    assert any(d["run_id"] == rid for d in rs.list_dead_letters())


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
