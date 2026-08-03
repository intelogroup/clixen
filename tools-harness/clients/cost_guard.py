"""
Daily token-usage budget guard for cloud model calls.

Nothing previously stopped runaway cloud spend — MAX_ROUNDS/max_steps bound
loop length, not tokens or dollars, and the 2026-07 revamp made cloud the
default for everything. This persists a simple daily token counter to disk
(survives restarts, unlike cloud_client's in-memory _usage_totals) and raises
BudgetExceededError once today's usage is at/over budget, so callers can fall
back to local gemma4 instead of erroring out to the user.
"""

import json
import logging
import os
import threading
from datetime import date
from pathlib import Path

_log = logging.getLogger(__name__)

_USAGE_FILE = Path(__file__).resolve().parent.parent / "data" / "cloud_usage.json"
# Sane default so the guard is actually on for a fresh user (the previous 1e12
# default meant unlimited cloud spend unless someone set the env var). 5M
# tokens/day ≈ $4-8 on DeepSeek; override via CLOUD_DAILY_TOKEN_BUDGET.
DAILY_TOKEN_BUDGET = int(os.environ.get("CLOUD_DAILY_TOKEN_BUDGET", 5_000_000))

_lock = threading.Lock()


class BudgetExceededError(Exception):
    """Raised when today's cumulative cloud token usage is at/over budget."""


def _read() -> dict:
    if not _USAGE_FILE.exists():
        return {"date": str(date.today()), "prompt_tokens": 0, "completion_tokens": 0}
    try:
        data = json.loads(_USAGE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"date": str(date.today()), "prompt_tokens": 0, "completion_tokens": 0}
    if data.get("date") != str(date.today()):
        return {"date": str(date.today()), "prompt_tokens": 0, "completion_tokens": 0}
    return data


def _write(data: dict) -> None:
    _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _USAGE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(_USAGE_FILE)


def _locked_read_modify_write(mutate) -> None:
    """Serialize the counter read-modify-write ACROSS processes (worker + chat_ui
    threads run in different processes and the threading.Lock only guards one).
    fcntl.flock on a sidecar lockfile; best-effort — if flock is unavailable or
    fails, fall back to the in-process lock only (still better than nothing)."""
    lock_path = _USAGE_FILE.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            with _lock:
                mutate()
    except (ImportError, OSError):
        with _lock:
            mutate()


def record_usage(prompt_tokens: int, completion_tokens: int) -> None:
    """Add today's usage. Resets automatically on date rollover (see _read())."""
    def _do() -> None:
        data = _read()
        data["prompt_tokens"] += prompt_tokens or 0
        data["completion_tokens"] += completion_tokens or 0
        _write(data)
    _locked_read_modify_write(_do)


def today_usage() -> dict:
    with _lock:
        return _read()


def check_budget() -> None:
    """Raise BudgetExceededError if today's total tokens are already at/over budget."""
    data = today_usage()
    total = data["prompt_tokens"] + data["completion_tokens"]
    if total >= DAILY_TOKEN_BUDGET:
        _log.warning(
            "[cost_guard] daily budget exceeded: %d/%d tokens used today",
            total, DAILY_TOKEN_BUDGET,
        )
        raise BudgetExceededError(
            f"Daily cloud token budget exceeded ({total}/{DAILY_TOKEN_BUDGET})"
        )
