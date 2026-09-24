"""
One run, one process (M3).

    python -m jobs.run_child --run-id RID [--model M] [--tools a,b] [--system S]

The child claims the execution lease, heartbeats while it works, drives the
journal loop, and releases the lease on exit. Crashing is SAFE by design: the
journal is the state, and the supervisor's reap-never-adopt policy spawns a
FRESH child that resumes from the last event (never reattaching to this one).
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):  # `python -m` sets it; direct execution does not
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fake_model_fn(script_path: str):
    """Scripted model for tests/CI: reads {rounds: [...]} JSON. Unregistered
    tool names still execute through the loop (they are journaled as calls),
    which is what the supervisor tests need — no provider, no tool registry."""
    import json

    script = json.loads(Path(script_path).read_text())
    rounds = script.get("rounds") or [{"text": "done", "tool_calls": []}]
    calls = {"n": 0}

    def model_fn(messages, tools):
        i = min(calls["n"], len(rounds) - 1)
        calls["n"] += 1
        if os.environ.get("CLIXEN_FAKE_CRASH_AFTER_ROUND") == "1" and calls["n"] > 1:
            os._exit(70)  # hard crash mid-run, like a SIGKILLed child
        if os.environ.get("CLIXEN_FAKE_RAISE_AFTER_ROUND") == "1" and calls["n"] > 1:
            # Python-level failure: propagates out of run_rounds so run_child's
            # handler must mark the run failed (vs the hard-crash path, which
            # the supervisor heals after the lease expires)
            raise RuntimeError("synthetic provider failure")
        return rounds[i]

    return model_fn


def _build_model(model: str, tools: list[str], fake_model: str | None):
    if fake_model:
        return _fake_model_fn(fake_model)
    from agents import cloud_adapter
    from tools._registry.schemas import ALL_TOOLS

    names = set(tools or [])
    schemas = [t for t in ALL_TOOLS if t.get("function", {}).get("name") in names]
    return cloud_adapter.make_model_fn(model, schemas)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="clixen run child")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--model", default="deepseek/deepseek-v4-flash")
    ap.add_argument("--tools", default="")
    ap.add_argument("--system", default="")
    ap.add_argument("--lease-ttl", type=float, default=60.0)
    ap.add_argument("--fake-model", default=None,
                    help="path to a scripted-rounds JSON (tests/CI only)")
    args = ap.parse_args(argv)

    from store import run_store as rs

    run = rs.get_run(args.run_id)
    if run is None:
        print(f"[run_child] no run {args.run_id!r}", file=sys.stderr)
        return 2

    tools = [t for t in (args.tools or "").split(",") if t]
    try:
        rs.claim_lease(args.run_id, pid=os.getpid(), ttl_s=args.lease_ttl)
    except (KeyError, ValueError) as exc:
        print(f"[run_child] cannot claim: {exc}", file=sys.stderr)
        return 2
    if rs.get_run(args.run_id)["status"] == "failed" and rs.list_dead_letters():
        return 3  # dead-lettered: the claim that hits the cap never executes

    stop = threading.Event()
    interval = max(2.0, args.lease_ttl / 3.0)

    # Seed the run's goal as the first user turn when the journal has none
    # (runs created by hand, or a journal truncated before its first turn).
    # Without this the model would be called with an empty message list.
    if not any(e["kind"] == "user_msg" for e in rs.get_events(args.run_id)):
        _goal = (rs.get_run(args.run_id) or {}).get("goal") or ""
        if _goal:
            rs.append_event(args.run_id, "user_msg", {"text": _goal, "via": "goal"})

    def heartbeat():
        while not stop.wait(interval):
            try:
                rs.renew_lease(args.run_id, ttl_s=args.lease_ttl)
            except Exception as exc:  # noqa: BLE001 — a beat must never kill the run
                print(f"[run_child] heartbeat failed: {exc}", file=sys.stderr)

    beat = threading.Thread(target=heartbeat, name="run-heartbeat", daemon=True)
    beat.start()
    try:
        from agents import run_loop

        run_loop.run_rounds(args.run_id, _build_model(args.model, tools, args.fake_model),
                            tools=tools, system=args.system)
    except Exception as exc:  # noqa: BLE001 — surface, then fail the run cleanly
        print(f"[run_child] run failed: {exc}", file=sys.stderr)
        # A crashed child must NOT leave a zombie: releasing the lease while
        # the run stays `running` means nothing would ever heal it (the
        # supervisor only reaps runs with an EXPIRED lease). Mark it failed
        # with the journal preserved — resumable, per the state table.
        try:
            rs.append_event(args.run_id, "heartbeat", {"reason": f"crash: {exc}"})
            if rs.get_run(args.run_id)["status"] == "running":
                rs.append_event(args.run_id, "status", {
                    "status": "failed", "reason": f"child crashed: {exc}"[:400]})
                rs.set_status(args.run_id, "failed")
        except Exception:  # noqa: BLE001
            pass
        return 1
    finally:
        stop.set()
        try:
            rs.release_lease(args.run_id)
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
