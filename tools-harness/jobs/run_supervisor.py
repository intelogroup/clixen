"""
Run supervisor (M3) — spawns one child process per run and enforces
reap-never-adopt:

  * a child that holds a live lease is left alone;
  * a child whose lease expired (crash, SIGKILL, power loss) is KILLED if any
    orphan pid remains, and the run is resumed by spawning a FRESH child that
    continues from the journal;
  * a run that burns its attempt budget is dead-lettered, never retried
    forever.

The supervisor never reattaches to a running child (no IPC adoption
protocol, no dual-writer risk) — the plan's locked orphan policy.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_HARNESS = str(Path(__file__).resolve().parent.parent)


def _kill(pid: int, grace_s: float = 5.0) -> None:
    """SIGTERM → wait → SIGKILL. Never blocks the supervisor for long."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def spawn_run(run_id: str, *, model: str = "deepseek/deepseek-v4-flash",
              tools: list[str] | None = None, system: str = "",
              lease_ttl: float = 60.0, fake_model: str | None = None,
              env: dict | None = None) -> subprocess.Popen:
    """Start `python -m jobs.run_child` for one run and return the process."""
    argv = [sys.executable, "-m", "jobs.run_child", "--run-id", run_id,
            "--model", model, "--tools", ",".join(tools or []),
            "--system", system, "--lease-ttl", str(lease_ttl)]
    if fake_model:
        argv += ["--fake-model", fake_model]
    child_env = {**os.environ, "PYTHONPATH": _HARNESS,
                 "PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
    return subprocess.Popen(argv, env=child_env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=False)


def reap_orphans(grace_s: float = 5.0, spawn: bool = True,
                 model: str = "deepseek/deepseek-v4-flash",
                 lease_ttl: float = 60.0) -> list[str]:
    """Handle runs whose lease expired: kill any orphan pid, then (unless
    `spawn=False`) start a fresh child that resumes from the journal. Returns
    the run_ids that were reaped. Dead-lettered runs are left alone."""
    from store import run_store as rs

    reaped: list[str] = []
    for run in rs.stale_runs():
        rid = run["run_id"]
        if run["pid"]:
            _kill(run["pid"], grace_s=grace_s)
        rs.release_lease(rid)
        rs.append_event(rid, "heartbeat", {"reason": "orphan reaped"})
        reaped.append(rid)
        if spawn:
            spawn_run(rid, model=model, lease_ttl=lease_ttl)
    return reaped


def tick(grace_s: float = 5.0, model: str = "deepseek/deepseek-v4-flash",
         lease_ttl: float = 60.0) -> list[str]:
    """One supervisor cycle — call from the worker's poll loop."""
    from store import run_store as rs

    rs.prune_runs()
    return reap_orphans(grace_s=grace_s, spawn=True, model=model, lease_ttl=lease_ttl)
