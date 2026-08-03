"""
Coverage for jobs/worker.py's bounded-concurrency job dispatch.

_execute_job's per-task timeout for _TASK_MODULES tasks uses signal.alarm(),
which only fires on the main thread — those jobs must stay sequential.
Everything else (the harness.run() branch, previously unbounded/sequential)
is I/O-bound and safe to run through a thread pool. This pins both behaviors:
concurrent jobs actually overlap in wall-clock time, and _TASK_MODULES jobs
still run one at a time on the calling thread.

All job execution is mocked — no real LLM/network calls, no signal handling.
"""
import threading
import time
from unittest.mock import patch

from jobs import worker


class _StopLoop(BaseException):
    """Raised from the mocked time.sleep to escape worker.main()'s while True after one cycle.

    Must NOT subclass Exception: worker.main()'s inner loop has a deliberately
    broad `except Exception: _log.exception(...)` (daemon must never crash on a
    bad job) that would otherwise swallow this and log+retry forever instead of
    letting it escape to the `except _StopLoop` below — that's what hung the
    suite (infinite loop, unbounded memory growth).
    """


def _job(job_id: str, task_name: str) -> dict:
    return {"id": job_id, "task_name": task_name, "params_json": "{}"}


def _run_main_one_cycle(**patches):
    """worker.main() parses sys.argv via argparse and loops forever — pin argv to a
    clean value and let the patched time.sleep raise _StopLoop to end the loop
    after exactly one poll cycle."""
    with patch.object(worker.sys, "argv", ["worker.py"]), \
         patch.object(worker, "_setup_handler_registry"), \
         patch.object(worker, "_poll_workflow_store"), \
         patch.object(worker.time, "sleep", side_effect=_StopLoop), \
         patches["claim_next"], patches["execute_job"]:
        try:
            worker.main()
        except _StopLoop:
            pass


def test_concurrent_jobs_overlap_in_wall_clock_time():
    """Two non-_TASK_MODULES jobs, each sleeping 0.3s inside _execute_job, must
    finish in well under 2x0.3s if actually dispatched through the thread pool."""
    jobs = [_job("a", "some_llm_task"), _job("b", "some_llm_task")]
    claim_sequence = iter(jobs + [None])

    def _slow_execute(job):
        time.sleep(0.3)

    start = time.time()
    _run_main_one_cycle(
        claim_next=patch.object(worker.job_queue, "claim_next", side_effect=lambda: next(claim_sequence)),
        execute_job=patch.object(worker, "_execute_job", side_effect=_slow_execute),
    )
    elapsed = time.time() - start

    assert elapsed < 0.5  # sequential would be >= 0.6s


def test_task_modules_jobs_stay_sequential_on_the_calling_thread():
    """_TASK_MODULES jobs rely on signal.alarm() for their timeout, which only
    works on the main thread — they must never be handed to the thread pool."""
    jobs = [_job("a", "morning_briefing"), _job("b", "some_llm_task")]
    claim_sequence = iter(jobs + [None])
    seen_threads = {}

    def _record_thread(job):
        seen_threads[job["id"]] = threading.current_thread() is threading.main_thread()

    _run_main_one_cycle(
        claim_next=patch.object(worker.job_queue, "claim_next", side_effect=lambda: next(claim_sequence)),
        execute_job=patch.object(worker, "_execute_job", side_effect=_record_thread),
    )

    assert seen_threads["a"] is True   # morning_briefing (_TASK_MODULES) ran on main thread
    assert seen_threads["b"] is False  # non-_TASK_MODULES ran in the pool


# --- Edge cases -------------------------------------------------------------

def test_empty_claim_cycle_does_not_crash_or_create_a_pool():
    """No jobs claimed this cycle — must not construct a ThreadPoolExecutor
    at all, just fall through to the workflow_store poll and sleep."""
    claim_sequence = iter([None])

    with patch.object(worker, "ThreadPoolExecutor") as mock_pool_cls:
        _run_main_one_cycle(
            claim_next=patch.object(worker.job_queue, "claim_next", side_effect=lambda: next(claim_sequence)),
            execute_job=patch.object(worker, "_execute_job"),
        )

    mock_pool_cls.assert_not_called()


def test_all_task_modules_jobs_skips_pool_entirely():
    """Only signal-timeout-dependent jobs claimed this cycle — the pool must
    not be constructed even though jobs did run."""
    jobs = [_job("a", "morning_briefing"), _job("b", "tech_brief")]
    claim_sequence = iter(jobs + [None])
    executed = []

    with patch.object(worker, "ThreadPoolExecutor") as mock_pool_cls:
        _run_main_one_cycle(
            claim_next=patch.object(worker.job_queue, "claim_next", side_effect=lambda: next(claim_sequence)),
            execute_job=patch.object(worker, "_execute_job", side_effect=lambda j: executed.append(j["id"])),
        )

    mock_pool_cls.assert_not_called()
    assert executed == ["a", "b"]


def test_exception_in_concurrent_job_is_caught_and_does_not_stop_other_jobs():
    """One job in the pool raises unexpectedly (past _execute_job's own
    internal try/except, e.g. a bug in the dispatch code itself) — must be
    caught at the pool boundary, logged, and not prevent the other concurrent
    job in the same cycle from completing."""
    jobs = [_job("a", "some_llm_task"), _job("b", "some_llm_task")]
    claim_sequence = iter(jobs + [None])
    executed = []

    def _maybe_raise(job):
        if job["id"] == "a":
            raise RuntimeError("unexpected crash")
        executed.append(job["id"])

    # Must not raise out of _run_main_one_cycle / worker.main()
    _run_main_one_cycle(
        claim_next=patch.object(worker.job_queue, "claim_next", side_effect=lambda: next(claim_sequence)),
        execute_job=patch.object(worker, "_execute_job", side_effect=_maybe_raise),
    )

    assert executed == ["b"]  # job "b" still completed despite job "a" crashing


def test_more_than_five_jobs_available_only_claims_five_per_cycle():
    """claim_next() has no batch param — the outer range(5) caps claims per
    cycle even when more jobs are sitting in the queue."""
    jobs = [_job(str(i), "some_llm_task") for i in range(8)]
    claim_sequence = iter(jobs)  # 8 available, never returns None within this cycle
    executed = []

    _run_main_one_cycle(
        claim_next=patch.object(worker.job_queue, "claim_next", side_effect=lambda: next(claim_sequence)),
        execute_job=patch.object(worker, "_execute_job", side_effect=lambda j: executed.append(j["id"])),
    )

    # claim_next() has no batch param — the outer range(5) caps claims per
    # cycle even when more jobs are sitting in the queue. Exactly 5 must be
    # claimed; compare as a set because the 3-worker pool completes them in
    # arbitrary order (as_completed), so append order is not deterministic.
    assert len(executed) == 5
    assert set(executed) == {"0", "1", "2", "3", "4"}
