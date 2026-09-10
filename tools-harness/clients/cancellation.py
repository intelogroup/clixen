import threading

class QueryAbortedException(Exception):
    """Raised when a query is aborted by the client/UI."""
    pass

def check_aborted():
    t = threading.current_thread()
    event = getattr(t, "abort_event", None)
    if event and event.is_set():
        raise QueryAbortedException("Query aborted by client")


# ---------------------------------------------------------------------------
# Run-id-keyed abort registry — for orphaned background work, not the
# per-thread check_aborted() above (that one only reaches a thread that had
# .abort_event set on it directly, which a ThreadPoolExecutor worker never
# gets by default).
#
# 2026-09-10: _run_subagent() (tools/orchestrator_tools.py) submits
# harness._execute_intent_pipeline to a 1-worker pool and bounds the wait
# with future.result(timeout=...). On timeout it returns a "[subagent
# timeout]" placeholder and the orchestrator moves on — but shutdown(wait=
# False) does NOT stop the worker thread, which keeps running the full
# LangGraph local-agent loop (more LLM rounds, more tool calls, real cost)
# with nothing left listening for its result. Confirmed live: a timed-out
# ask_local_agent call kept issuing "SENDING TO OLLAMA" rounds for minutes
# after the orchestrator had already answered from a different tool.
#
# run_id (already generated per subagent call and threaded through
# CURRENT_RUN_ID / trace_store) is the natural join key — mark it aborted
# here, and the local-agent step loop checks it once per step (same
# granularity as jobs/worker.py's cancel_event.wait() fix for the same
# underlying "timeout doesn't kill the thread" gotcha).
_aborted_runs: set[str] = set()
_aborted_lock = threading.Lock()
_ABORTED_RUNS_MAX = 500  # bounded — these are short-lived correlation ids


def abort_run(run_id: str) -> None:
    if not run_id:
        return
    with _aborted_lock:
        _aborted_runs.add(run_id)
        if len(_aborted_runs) > _ABORTED_RUNS_MAX:
            # Not FIFO-ordered (plain set) — fine, this is just a leak guard,
            # not a correctness-sensitive eviction policy.
            _aborted_runs.pop()


def is_run_aborted(run_id: str | None) -> bool:
    if not run_id:
        return False
    with _aborted_lock:
        return run_id in _aborted_runs
