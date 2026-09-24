"""Tests for agents/run_loop.py — stateless journal-driven loop (M1).

Model is injected (fake model_fn); tool execution is monkeypatched so the
full tool registry (heavy import graph) never loads. DB isolated per-test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents import run_loop
from store import run_store as rs


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "_DB_PATH", tmp_path / "run_store.sqlite")
    monkeypatch.setattr(rs, "_vault_secret_values", lambda: frozenset())
    yield


def _scripted(*results):
    """Fake model_fn returning scripted round results in order (last repeats)."""
    calls = {"n": 0}

    def model_fn(messages, tools):
        i = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        return results[i]

    return model_fn


def test_happy_path_tool_round_then_answer(monkeypatch):
    executed = []

    def fake_exec(name, args):
        executed.append((name, args))
        return "tool says hi"

    monkeypatch.setattr(run_loop, "_execute_tool", fake_exec)
    rid = rs.create_run("g")
    model = _scripted(
        {"text": "", "tool_calls": [{"id": "t1", "name": "get_current_time",
                                     "args": {"tz": "UTC"}}]},
        {"text": "final answer", "tool_calls": []},
    )
    out = run_loop.run_rounds(rid, model, tools=["get_current_time"])
    assert out == "final answer"
    assert executed == [("get_current_time", {"tz": "UTC"})]
    assert rs.get_run(rid)["status"] == "succeeded"
    # Event order: no assistant_msg for the tool round — its text was empty, and
    # replay_messages() already reconstructs the assistant tool_calls turn from
    # the tool_call event. A synthetic empty assistant_msg would inject a junk
    # assistant turn into the provider message list. Each completed round is
    # closed by a `round` boundary snapshot.
    kinds = [e["kind"] for e in rs.get_events(rid)]
    assert kinds == ["run", "status", "tool_call", "tool_result", "round",
                     "assistant_msg", "round", "status"]


def test_replay_cache_never_reexecutes_answered_call(monkeypatch):
    executed = []
    monkeypatch.setattr(run_loop, "_execute_tool",
                        lambda n, a: executed.append(n) or "cached")
    rid = rs.create_run("g")
    # Pre-seed a completed call from a "previous life"
    rs.append_event(rid, "tool_call", {"id": "t1", "name": "web_search",
                                       "args": {"q": "x"}})
    rs.append_event(rid, "tool_result", {"id": "t1", "result": "cached"})
    model = _scripted(
        {"text": "", "tool_calls": [{"id": "t1", "name": "web_search",
                                     "args": {"q": "x"}}]},  # same id again
        {"text": "done", "tool_calls": []},
    )
    out = run_loop.run_rounds(rid, model, tools=["web_search"])
    assert out == "done"
    assert executed == []  # replay cache: answered id never re-executed
    t1_events = [e for e in rs.get_events(rid)
                 if e["kind"] in ("tool_call", "tool_result")
                 and e["payload"].get("id") == "t1"]
    assert len(t1_events) == 2  # no duplicate append for the same id


def test_resume_continues_round_budget_from_journal(monkeypatch):
    """A resumed run must not get a fresh round budget: 18 rounds already spent
    of max_rounds=20 leaves exactly 2, and each executed round journals a
    round-boundary snapshot (plan WS1 issue 1)."""
    calls = {"n": 0}

    def model(messages, tools):
        calls["n"] += 1
        return {"text": f"partial {calls['n']}", "tool_calls": []}

    monkeypatch.setattr(run_loop, "_execute_tool", lambda n, a: "unused")
    rid = rs.create_run("g", policy={"max_rounds": 20})
    rs.set_status(rid, "running")
    rs.append_round_snapshot(rid, round_idx=18, model="deepseek/deepseek-v4-flash",
                             escalated=True, consecutive_errors=1)
    out = run_loop.run_rounds(rid, model, tools=[])
    assert out == "partial 1"  # 19th round answered → terminal success
    snaps = [e["payload"] for e in rs.get_events(rid) if e["kind"] == "round"]
    assert [s["round_idx"] for s in snaps] == [18, 19]
    assert snaps[-1]["escalated"] is True  # escalation state survived the resume


def test_round_budget_exhaustion_counts_prior_rounds(monkeypatch):
    monkeypatch.setattr(run_loop, "_execute_tool", lambda n, a: "unused")
    rid = rs.create_run("g", policy={"max_rounds": 3})
    rs.set_status(rid, "running")
    rs.append_round_snapshot(rid, round_idx=2, escalated=False, consecutive_errors=0)

    def model(messages, tools):
        return {"text": "never reached", "tool_calls": []}

    out = run_loop.run_rounds(rid, model, tools=[])
    assert out == ""
    assert rs.get_run(rid)["status"] == "budget-exceeded"
    reasons = [e["payload"].get("reason") for e in rs.get_events(rid)
               if e["kind"] == "budget"]
    assert any("max_rounds" in (r or "") for r in reasons)


@pytest.mark.timeout(90)
def test_real_crash_midrun_resume_replays_cache(tmp_path, monkeypatch):
    """Spawn a child that journals a tool_result then hangs; SIGKILL it; resume
    in-process. The answered tool must NOT re-execute (replay cache read from
    the journal, not memory) and the run must still reach its final answer."""
    import os
    import signal
    import subprocess
    import sys
    import time

    db = tmp_path / "crash.sqlite"
    marker = tmp_path / "marker.txt"
    driver = tmp_path / "driver.py"
    harness = str(Path(__file__).parent.parent)
    driver.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {harness!r})\n"
        "from store import run_store as rs\n"
        "from agents import run_loop\n"
        f"rs._DB_PATH = __import__('pathlib').Path({str(db)!r})\n"
        "rs._vault_secret_values = lambda: frozenset()\n"
        "MARKER = __import__('pathlib').Path(%r)\n"
        "def tool(name, args):\n"
        "    with MARKER.open('a') as fh:\n"
        "        fh.write('executed\\n')\n"
        "    return 'tool output'\n"
        "run_loop._execute_tool = tool\n"
        "def model(messages, tools):\n"
        "    if any(m.get('role') == 'tool' for m in messages):\n"
        "        raise SystemExit\n"
        "    return {'text': '', 'tool_calls': [{'id': 'c1', 'name': 'web_search',"
        " 'args': {'q': 'x'}}]}\n"
        "run_loop.run_rounds(sys.argv[1], model, tools=['web_search'])\n" % str(marker)
    )
    # Parent and child must share one journal file (the autouse fixture
    # points at its own temp path) — repoint BEFORE creating the run.
    rs._DB_PATH = db
    rid = rs.create_run("crashy")
    rs.set_status(rid, "running")
    env = {**os.environ, "PYTHONPATH": harness, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.Popen([sys.executable, str(driver), rid], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 30
        while time.time() < deadline and not marker.exists():
            if proc.poll() is not None:
                pytest.fail(f"driver died: {proc.stderr.read().decode()[-800:]}")
            time.sleep(0.05)
        assert marker.exists(), "child never executed the tool"
        time.sleep(0.3)  # let the child journal its tool_result before the kill
        os.kill(proc.pid, signal.SIGKILL)
    finally:
        proc.wait(timeout=10)

    results = [e for e in rs.get_events(rid) if e["kind"] == "tool_result"]
    assert len(results) == 1, "child did not journal the result before the crash"
    snap = rs.latest_round(rid)
    assert snap is None or snap["round_idx"] >= 0

    executed_after = []
    monkeypatch.setattr(run_loop, "_execute_tool",
                        lambda n, a: executed_after.append(n) or "re-exec")

    def final_model(messages, tools):
        return {"text": "final answer", "tool_calls": []}

    out = run_loop.run_rounds(rid, final_model, tools=["web_search"])
    assert out == "final answer"
    assert executed_after == [], "replay cache re-executed an answered call"
    assert marker.read_text().count("executed") == 1
    assert rs.get_run(rid)["status"] == "succeeded"


def test_pause_control_stops_the_run_at_the_next_boundary(monkeypatch):
    """A pause intent appended mid-run is applied when the round finishes:
    status becomes paused (resumable) and no further model round runs."""
    calls = {"n": 0}

    def model(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            rs.append_control(rid, "pause")
            return {"text": "", "tool_calls": [{"id": "c1", "name": "web_search",
                                                "args": {"q": "x"}}]}
        return {"text": "should not be reached", "tool_calls": []}

    monkeypatch.setattr(run_loop, "_execute_tool", lambda n, a: "result")
    rid = rs.create_run("g")
    out = run_loop.run_rounds(rid, model, tools=["web_search"])
    run = rs.get_run(rid)
    assert run["status"] == "paused" and run["resumable"] is True
    assert calls["n"] == 1
    assert out == ""


def test_kill_control_terminates_the_run(monkeypatch):
    calls = {"n": 0}

    def model(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            rs.append_control(rid, "kill")
            return {"text": "", "tool_calls": [{"id": "c1", "name": "web_search",
                                                "args": {"q": "x"}}]}
        return {"text": "unreached", "tool_calls": []}

    monkeypatch.setattr(run_loop, "_execute_tool", lambda n, a: "result")
    rid = rs.create_run("g")
    run_loop.run_rounds(rid, model, tools=["web_search"])
    run = rs.get_run(rid)
    assert run["status"] == "killed" and run["resumable"] is True
    assert calls["n"] == 1


def test_steer_control_becomes_a_user_message_for_the_next_round(monkeypatch):
    seen_messages = []

    def model(messages, tools):
        seen_messages.append(list(messages))
        if len(seen_messages) == 1:
            rs.append_control(rid, "steer", text="focus on pricing first")
            return {"text": "", "tool_calls": [{"id": "c1", "name": "web_search",
                                                "args": {"q": "x"}}]}
        return {"text": "adjusted answer", "tool_calls": []}

    monkeypatch.setattr(run_loop, "_execute_tool", lambda n, a: "result")
    rid = rs.create_run("g")
    out = run_loop.run_rounds(rid, model, tools=["web_search"])
    assert out == "adjusted answer"
    steer_msgs = [m for m in seen_messages[-1] if m.get("role") == "user"]
    assert any(m.get("content") == "focus on pricing first" for m in steer_msgs)
    # the steer was applied exactly once
    assert sum(1 for m in seen_messages[-1]
               if m.get("content") == "focus on pricing first") == 1


def test_consumed_controls_are_not_reapplied_on_resume(monkeypatch):
    """Life 1 consumes a steer and is killed; life 2 must not append it again —
    the consumed cursor survives in the round snapshot."""
    monkeypatch.setattr(run_loop, "_execute_tool", lambda n, a: "result")
    rid = rs.create_run("g", policy={"max_rounds": 5})

    def model_life1(messages, tools):
        rs.append_control(rid, "steer", text="old steer")
        rs.append_control(rid, "kill")
        return {"text": "", "tool_calls": [{"id": "c1", "name": "web_search",
                                            "args": {"q": "x"}}]}

    run_loop.run_rounds(rid, model_life1, tools=["web_search"])
    assert rs.get_run(rid)["status"] == "killed"

    seen = []

    def model_life2(messages, tools):
        seen.append(list(messages))
        return {"text": "final", "tool_calls": []}

    out = run_loop.run_rounds(rid, model_life2, tools=[])
    assert out == "final"
    steers = [m for m in seen[0] if m.get("content") == "old steer"]
    assert len(steers) == 1, f"steer re-applied on resume: {seen[0]}"
    journal_steers = [e for e in rs.get_events(rid)
                      if e["kind"] == "user_msg" and e["payload"].get("text") == "old steer"]
    assert len(journal_steers) == 1


def test_dangling_side_effecting_call_fails_run(monkeypatch):
    monkeypatch.setattr(run_loop, "_execute_tool",
                        lambda n, a: (_ for _ in ()).throw(AssertionError("exec!")))
    rid = rs.create_run("g")
    rs.set_status(rid, "running")
    # Crash simulation: tool_call appended, no tool_result (not in REPLAYABLE)
    rs.append_event(rid, "tool_call", {"id": "t9", "name": "browser_click_ref",
                                       "args": {"ref": "e3"}})
    model = _scripted(
        {"text": "", "tool_calls": [{"id": "t9", "name": "browser_click_ref",
                                     "args": {"ref": "e3"}}]},
    )
    out = run_loop.run_rounds(rid, model, tools=["browser_click_ref"])
    run = rs.get_run(rid)
    assert run["status"] == "failed" and run["resumable"] is True
    reasons = [e["payload"].get("reason") for e in rs.get_events(rid)
               if e["kind"] == "status"]
    assert any(r and "dangling" in r for r in reasons)
    assert out == ""  # nothing executed


def test_dangling_replayable_tool_reexecutes(monkeypatch):
    executed = []
    monkeypatch.setattr(run_loop, "_execute_tool",
                        lambda n, a: executed.append(n) or "fresh")
    rid = rs.create_run("g")
    rs.set_status(rid, "running")
    rs.append_event(rid, "tool_call", {"id": "t5", "name": "web_search",
                                       "args": {"q": "y"}})  # REPLAYABLE
    model = _scripted(
        {"text": "", "tool_calls": [{"id": "t5", "name": "web_search",
                                     "args": {"q": "y"}}]},
        {"text": "ok", "tool_calls": []},
    )
    assert run_loop.run_rounds(rid, model, tools=["web_search"]) == "ok"
    assert executed == ["web_search"]
    assert rs.get_run(rid)["status"] == "succeeded"