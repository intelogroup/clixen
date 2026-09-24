"""
Run service (M2) — the surface a chat/UI calls into.

    run_id = start_run(goal, tools=...)        # returns immediately
    for ev in stream_events(run_id): ...       # journal tail (SSE source)
    pause(run_id) / resume(run_id) / kill(run_id) / steer(run_id, text)

Design notes:
  * The journal is the only state. `stream_events` tails it by seq, so a
    reconnecting client resumes with `after_seq` (SSE Last-Event-ID).
  * Controls are journaled INTENTS; the executing loop applies them at its
    next round boundary (see agents/run_loop.py). The service never mutates
    run status — transitions stay single-writer in the loop.
  * Execution defaults to a daemon thread; M3 moves it to a spawned child
    process (same journal contract, so nothing above this line changes).
"""
from __future__ import annotations

import os
import threading
import time

from store import run_store as rs

DEFAULT_MODEL = "deepseek/deepseek-v4-flash"


def _default_execute(run_id: str, model: str, tools: list[str], system: str,
                     policy: dict | None = None) -> None:
    """Background execution: journal loop + the live cloud adapter."""
    from agents import cloud_adapter, run_loop
    from tools._registry.schemas import ALL_TOOLS

    names = set(tools or [])
    schemas = [t for t in ALL_TOOLS
               if t.get("function", {}).get("name") in names]
    model_fn = cloud_adapter.make_model_fn(model, schemas)
    try:
        run_loop.run_rounds(run_id, model_fn, tools=list(names), system=system)
    except Exception as exc:  # noqa: BLE001 — a background failure must be visible
        try:
            rs.append_event(run_id, "status", {"status": "failed",
                                                "reason": f"run crashed: {exc}"})
            rs.set_status(run_id, "failed")
        except Exception:  # noqa: BLE001
            pass


def start_run(goal: str, tools: list[str] | None = None, *, model: str = DEFAULT_MODEL,
              system: str = "", policy: dict | None = None,
              trigger: str = "chat-send", execute=None, mode: str | None = None) -> str:
    """Journal the run + goal and kick off execution. Returns the run_id —
    the caller never waits for the answer (plan IO contract: the sender is
    never blocked; progress arrives on the event stream).

    mode: "thread" (default) runs in a daemon thread; "process" spawns a
    supervised child process (M3). Env `CLIXEN_RUN_MODE` sets the default.
    """
    rid = rs.create_run(goal, trigger=trigger, policy=policy or {})
    rs.append_event(rid, "user_msg", {"text": goal})
    if execute is not None:
        execute(rid)  # explicit executor (tests / synchronous callers)
        return rid
    if (mode or os.environ.get("CLIXEN_RUN_MODE", "thread")) == "process":
        from jobs import run_supervisor

        run_supervisor.spawn_run(rid, model=model, tools=tools or [], system=system)
        return rid
    threading.Thread(
        target=_default_execute,
        args=(rid, model, tools or [], system, policy),
        name=f"run-{rid[:8]}", daemon=True,
    ).start()
    return rid


def resume_run(run_id: str, *, model: str = DEFAULT_MODEL, tools: list[str] | None = None,
               system: str = "", mode: str | None = None) -> str:
    """Resume a resumable run: journal the intent, then either wake an inline
    thread or spawn a fresh supervised child (never adopt the old one)."""
    run = rs.get_run(run_id)
    if run is None:
        return f"[runs] no run {run_id!r}"
    if not run["resumable"]:
        return f"[runs] Run {run['status']} is not resumable — start a new run."
    rs.append_control(run_id, "resume")
    if (mode or os.environ.get("CLIXEN_RUN_MODE", "thread")) == "process":
        from jobs import run_supervisor

        run_supervisor.spawn_run(run_id, model=model, tools=tools or [], system=system)
    else:
        threading.Thread(
            target=_default_execute, args=(run_id, model, tools or [], system,
                                           run.get("policy") or {}),
            name=f"run-{run_id[:8]}", daemon=True).start()
    return f"Resuming run {run_id[:8]}."


def stream_events(run_id: str, after_seq: int = 0, poll_s: float = 0.25,
                  max_wait_s: float = 300.0):
    """Yield journal events after `after_seq`, then stop once the run reaches
    a terminal state and the journal is drained. Backs SSE / Last-Event-ID."""
    deadline = time.monotonic() + max_wait_s
    cursor = after_seq
    while time.monotonic() < deadline:
        batch = rs.get_events(run_id, after_seq=cursor)
        for ev in batch:
            cursor = ev["seq"]
            yield ev
        run = rs.get_run(run_id)
        if run and run["status"] in rs.TERMINAL and not rs.get_events(run_id, after_seq=cursor):
            return
        if not batch:
            time.sleep(poll_s)


def _guard(run_id: str, verb: str) -> str | None:
    run = rs.get_run(run_id)
    if run is None:
        return f"[runs] no run {run_id!r}"
    if run["status"] in rs.TERMINAL:
        return f"[runs] Run already finished ({run['status']}) — start a new run."
    return None


def pause(run_id: str) -> str:
    if err := _guard(run_id, "pause"):
        return err
    rs.append_control(run_id, "pause")
    return f"Pausing run {run_id[:8]} — it stops at the next round boundary."


def resume(run_id: str) -> str:
    if err := _guard(run_id, "resume"):
        return err
    rs.append_control(run_id, "resume")
    return f"Resuming run {run_id[:8]}."


def kill(run_id: str) -> str:
    if err := _guard(run_id, "kill"):
        return err
    rs.append_control(run_id, "kill")
    return f"Killing run {run_id[:8]} — journal preserved."


def steer(run_id: str, text: str) -> str:
    if err := _guard(run_id, "steer"):
        return err
    rs.append_control(run_id, "steer", text=text)
    return f"Steered run {run_id[:8]} — applies next round."


def run_card(run_id: str) -> dict:
    """The instant run-card payload the UI shows on send (IA: status first)."""
    run = rs.get_run(run_id)
    if run is None:
        return {}
    snap = rs.latest_round(run_id) or {}
    return {
        "run_id": run["run_id"], "goal": run["goal"], "status": run["status"],
        "resumable": run["resumable"], "round": (snap.get("round_idx", -1) + 1),
        "last_seq": run["last_seq"],
    }
