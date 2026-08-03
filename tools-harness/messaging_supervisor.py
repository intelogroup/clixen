#!/usr/bin/env python3
"""
Supervisor: runs telegram_bot, whatsapp_bot, and whatsapp_bridge independently
— one crashing doesn't take down the other two. Python rewrite of
messaging_supervisor.sh: adds a real per-process state record (instead of
bash's flat eval'd `_BW_name`/`_RT_name` vars), a graceful SIGTERM -> 5s grace
-> SIGKILL escalation on both shutdown and forced restarts (the old script
only ever did a blind `kill`, no escalation), and an HTTP health-check-based
hang detector for whatsapp_bot/whatsapp_bridge — their known failure mode is
hanging while still alive, which `kill -0` liveness polling can't see at all.
telegram_bot.py has no HTTP surface (python-telegram-bot long-poller, not
FastAPI) so it stays liveness-only, same as before.

stdout/stderr are left inherited (same fds launchd redirects to
messaging_stdout.log/messaging_stderr.log) — unchanged from the bash version,
so crash_watchdog.py's existing text-tail of those files keeps working
without modification.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_HARNESS_DIR = Path(__file__).parent
_VENV_PYTHON = str(_HARNESS_DIR.parent / ".venv" / "bin" / "python")
_NODE = "/opt/homebrew/bin/node"

_GRACE_SECS = 5.0          # SIGTERM -> SIGKILL escalation window (whatsapp_bot/bridge)
_GRACE_SECS_TELEGRAM = 45.0  # telegram_bot can be mid local-agent tool loop (30-60s+) —
                              # a short grace here SIGKILLs an in-flight user reply mid-turn
                              # (confirmed live: this exact thing happened during testing).
_BACKOFF_RESET_SECS = 60.0  # uptime after which a crash's backoff resets to base
_BACKOFF_BASE = 2.0
_BACKOFF_CAP = 30.0
_HEALTH_POLL_SECS = 30.0    # how often to hit /status endpoints
_HEALTH_FAIL_THRESHOLD = 3  # consecutive failures before treating it as a hang
_MAIN_POLL_SECS = 2.0


@dataclass
class ManagedProcess:
    name: str
    cmd: list[str]
    health_url: str | None = None  # None = liveness-only (no HTTP surface)
    grace_secs: float = _GRACE_SECS
    proc: subprocess.Popen | None = None
    started_at: float = 0.0
    backoff: float = _BACKOFF_BASE
    last_restart: float = 0.0
    consecutive_health_failures: int = 0
    state: str = "exited"  # starting | running | exiting | exited


def _start(mp: ManagedProcess) -> None:
    mp.proc = subprocess.Popen(mp.cmd, cwd=str(_HARNESS_DIR))
    mp.started_at = time.time()
    mp.state = "running"
    mp.consecutive_health_failures = 0
    print(f"[supervisor] started {mp.name} pid={mp.proc.pid}", flush=True)


def _is_alive(mp: ManagedProcess) -> bool:
    return mp.proc is not None and mp.proc.poll() is None


def _graceful_kill(mp: ManagedProcess) -> None:
    """SIGTERM, wait up to mp.grace_secs, SIGKILL if still alive. The old bash
    supervisor just called `kill $PID` once with no escalation — a child that
    ignores SIGTERM (or is wedged) would never actually die."""
    if not _is_alive(mp):
        return
    mp.state = "exiting"
    mp.proc.terminate()
    deadline = time.time() + mp.grace_secs
    while time.time() < deadline:
        if mp.proc.poll() is not None:
            mp.state = "exited"
            return
        time.sleep(0.2)
    print(f"[supervisor] {mp.name} did not exit within {mp.grace_secs}s of SIGTERM, sending SIGKILL", flush=True)
    mp.proc.kill()
    mp.proc.wait(timeout=5)
    mp.state = "exited"


def _apply_backoff(mp: ManagedProcess) -> None:
    now = time.time()
    uptime = now - mp.started_at if mp.started_at else 0
    if uptime > _BACKOFF_RESET_SECS:
        mp.backoff = _BACKOFF_BASE
    time.sleep(mp.backoff)
    mp.backoff = min(mp.backoff * 2, _BACKOFF_CAP)
    mp.last_restart = now


def _check_health(mp: ManagedProcess) -> bool:
    """GET the process's /status endpoint. Returns True if healthy. A process
    that's alive-but-wedged (accepting no connections, or hanging on the
    request) fails this the same as one that's actually down — that's the
    point: `kill -0` can't distinguish 'running fine' from 'running but stuck',
    this can."""
    if mp.health_url is None:
        return True
    try:
        with urllib.request.urlopen(mp.health_url, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _build_processes() -> list[ManagedProcess]:
    return [
        ManagedProcess(
            name="telegram_bot",
            cmd=[_VENV_PYTHON, str(_HARNESS_DIR / "telegram_bot.py")],
            health_url=None,  # ponytail: no HTTP surface on this bot (long-poller); add one if a hang shows up here too
            grace_secs=_GRACE_SECS_TELEGRAM,
        ),
        ManagedProcess(
            name="whatsapp_bot",
            cmd=[_VENV_PYTHON, str(_HARNESS_DIR / "whatsapp_bot.py")],
            health_url="http://localhost:9236/status",
        ),
        ManagedProcess(
            name="whatsapp_bridge",
            cmd=[
                _NODE,
                str(_HARNESS_DIR / "node_modules/tsx/dist/cli.mjs"),
                str(_HARNESS_DIR / "whatsapp_bridge.ts"),
            ],
            health_url="http://localhost:9235/status",
        ),
    ]


def main() -> None:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("WHATSAPP_BRIDGE_URL", "http://localhost:9235")
    env.setdefault("WHATSAPP_BOT_PORT", "9236")
    env.setdefault("WHATSAPP_BRIDGE_PORT", "9235")
    env.setdefault("WHATSAPP_BOT_URL", "http://localhost:9236")
    env.setdefault("NODE_ENV", "production")
    os.environ.update(env)

    processes = _build_processes()
    for mp in processes:
        _start(mp)

    _shutting_down = {"flag": False}

    def _cleanup(signum=None, frame=None) -> None:
        if _shutting_down["flag"]:
            return
        _shutting_down["flag"] = True
        print("[supervisor] stopping all messaging processes...", flush=True)
        for mp in processes:
            _graceful_kill(mp)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGHUP, _cleanup)

    last_health_check = 0.0

    try:
        while True:
            now = time.time()

            for mp in processes:
                if not _is_alive(mp):
                    print(f"[supervisor] {mp.name} exited, restarting it (only)...", flush=True)
                    mp.state = "exited"
                    _apply_backoff(mp)
                    _start(mp)

            if now - last_health_check >= _HEALTH_POLL_SECS:
                last_health_check = now
                for mp in processes:
                    if mp.health_url is None or not _is_alive(mp):
                        continue
                    if _check_health(mp):
                        mp.consecutive_health_failures = 0
                    else:
                        mp.consecutive_health_failures += 1
                        print(f"[supervisor] {mp.name} health check failed ({mp.consecutive_health_failures}/{_HEALTH_FAIL_THRESHOLD})", flush=True)
                        if mp.consecutive_health_failures >= _HEALTH_FAIL_THRESHOLD:
                            print(f"[supervisor] {mp.name} unresponsive for {_HEALTH_FAIL_THRESHOLD} checks, forcing restart (alive but hung)", flush=True)
                            _graceful_kill(mp)
                            _apply_backoff(mp)
                            _start(mp)

            time.sleep(_MAIN_POLL_SECS)

    except KeyboardInterrupt:
        _cleanup()


if __name__ == "__main__":
    main()
