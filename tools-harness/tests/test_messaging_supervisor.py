"""
Regression tests for messaging_supervisor.py (replaces the old bash
messaging_supervisor.sh — see test_supervisor_backoff.sh, deleted). Covers the
three behaviors that motivated the rewrite: per-process backoff/reset, real
SIGTERM->grace->SIGKILL escalation (the bash version only ever did a blind
`kill`), and the HTTP health-check hang detector (whatsapp_bot/bridge's known
failure mode is hanging while still alive, invisible to plain liveness checks).
"""
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import messaging_supervisor as ms


def _mp(name="proc", grace_secs=1.0, health_url=None):
    return ms.ManagedProcess(name=name, cmd=["/bin/true"], health_url=health_url, grace_secs=grace_secs)


def test_apply_backoff_doubles_and_caps():
    mp = _mp()
    mp.started_at = time.time()  # fresh process, uptime ~0 -> no reset
    with patch("time.sleep"):  # don't actually wait during the test
        assert mp.backoff == ms._BACKOFF_BASE
        ms._apply_backoff(mp)
        assert mp.backoff == ms._BACKOFF_BASE * 2
        ms._apply_backoff(mp)
        assert mp.backoff == ms._BACKOFF_BASE * 4
        # keep doubling past the cap
        for _ in range(10):
            ms._apply_backoff(mp)
        assert mp.backoff == ms._BACKOFF_CAP


def test_apply_backoff_resets_after_uptime():
    mp = _mp()
    mp.backoff = ms._BACKOFF_CAP
    mp.started_at = time.time() - (ms._BACKOFF_RESET_SECS + 1)  # long-lived process
    with patch("time.sleep"):
        ms._apply_backoff(mp)
    assert mp.backoff == ms._BACKOFF_BASE * 2  # reset to base, then doubled once


def test_graceful_kill_escalates_to_sigkill_when_process_ignores_sigterm():
    mp = _mp(name="ignores_sigterm", grace_secs=1.0)
    mp.proc = subprocess.Popen(["/bin/sh", "-c", "trap '' TERM; sleep 30"])
    mp.state = "running"
    time.sleep(0.3)  # let the shell actually install the trap before we SIGTERM it —
    # without this, terminate() can race the shell's own startup and land before
    # `trap '' TERM` executes, hitting default SIGTERM behavior instead (flaky test).
    try:
        t0 = time.time()
        ms._graceful_kill(mp)
        elapsed = time.time() - t0
        assert mp.state == "exited"
        assert mp.proc.poll() is not None
        # Should have waited out the grace period before escalating, not killed instantly.
        assert elapsed >= 1.0
    finally:
        if mp.proc.poll() is None:
            mp.proc.kill()


def test_graceful_kill_exits_promptly_on_clean_sigterm():
    mp = _mp(name="exits_cleanly", grace_secs=10.0)
    mp.proc = subprocess.Popen(["/bin/sh", "-c", "trap 'exit 0' TERM; sleep 30 & wait"])
    mp.state = "running"
    try:
        t0 = time.time()
        ms._graceful_kill(mp)
        elapsed = time.time() - t0
        assert mp.state == "exited"
        # Should exit well before the 10s grace deadline, not get force-killed.
        assert elapsed < 5.0
    finally:
        if mp.proc.poll() is None:
            mp.proc.kill()


def test_check_health_true_on_200():
    mp = _mp(health_url="http://localhost:9999/status")

    class _FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        assert ms._check_health(mp) is True


def test_check_health_false_on_connection_error():
    mp = _mp(health_url="http://localhost:9999/status")
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        assert ms._check_health(mp) is False


def test_check_health_true_when_no_url_configured():
    mp = _mp(health_url=None)
    assert ms._check_health(mp) is True


if __name__ == "__main__":
    test_apply_backoff_doubles_and_caps()
    test_apply_backoff_resets_after_uptime()
    test_graceful_kill_escalates_to_sigkill_when_process_ignores_sigterm()
    test_graceful_kill_exits_promptly_on_clean_sigterm()
    test_check_health_true_on_200()
    test_check_health_false_on_connection_error()
    test_check_health_true_when_no_url_configured()
    print("messaging_supervisor tests passed")
