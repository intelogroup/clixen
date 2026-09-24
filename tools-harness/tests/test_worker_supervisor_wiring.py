"""M3 production wiring: the worker poll loop must run the run supervisor, so
crashed run-children are reaped and resumed without a human.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobs import worker
from jobs import run_supervisor as sup
from store import run_store as rs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    db = tmp_path / "sup.sqlite"
    monkeypatch.setenv("CLIXEN_RUN_STORE", str(db))
    monkeypatch.setattr(rs, "_DB_PATH", db)
    monkeypatch.setattr(rs, "_vault_secret_values", lambda: frozenset())


def test_supervisor_cycle_calls_tick(monkeypatch):
    calls = []
    monkeypatch.setattr(sup, "tick", lambda **kw: calls.append(kw) or [])
    assert worker._supervisor_cycle() == 0
    assert len(calls) == 1


def test_supervisor_cycle_swallows_failures(monkeypatch):
    """A supervisor bug must never take the worker daemon down."""
    def boom(**kw):
        raise RuntimeError("supervisor exploded")

    monkeypatch.setattr(sup, "tick", boom)
    assert worker._supervisor_cycle() == 0  # swallowed, loop continues


def test_supervisor_cycle_reports_reaped_runs(monkeypatch, caplog):
    monkeypatch.setattr(sup, "tick", lambda **kw: ["run1234abcd", "run5678efgh"])
    assert worker._supervisor_cycle() == 2


def test_supervisor_cycle_can_be_disabled(monkeypatch):
    monkeypatch.setenv("CLIXEN_RUN_SUPERVISOR", "0")
    monkeypatch.setattr(sup, "tick", lambda **kw: pytest.fail("must not run"))
    assert worker._supervisor_cycle() == 0


def test_main_loop_invokes_the_supervisor(monkeypatch):
    """The poll loop calls _supervisor_cycle every cycle (sentinel stops it)."""
    seen = []

    class _Stop(BaseException):
        pass

    def fake_sleep(_s):
        raise _Stop()

    monkeypatch.setattr(worker, "_setup_handler_registry", lambda: None)
    monkeypatch.setattr(worker, "_check_health", lambda **kw: True)
    monkeypatch.setattr(worker.job_queue, "claim_next", lambda: None)
    monkeypatch.setattr(worker.job_queue, "reap_stale_running", lambda **kw: [])
    monkeypatch.setattr(worker, "_supervisor_cycle",
                        lambda: seen.append(1) or 0)
    monkeypatch.setattr(worker.time, "sleep", fake_sleep)
    monkeypatch.setattr(sys, "argv", ["worker", "--poll-interval", "0"])
    with pytest.raises(_Stop):
        worker.main()
    assert seen, "the poll loop never ran the supervisor cycle"
