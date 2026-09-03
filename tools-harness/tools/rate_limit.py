"""Generic per-key cooldown, flock-guarded for cross-process safety.

Any tool can be rate-limited from one place (tools/registry.py::_execute_raw)
instead of each tool baking its own guard.
"""
from __future__ import annotations

import fcntl
import re
import time
from pathlib import Path

_STATE_DIR = Path(__file__).resolve().parent.parent / ".rate_limit_state"
_STATE_DIR.mkdir(exist_ok=True)
_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]")


def _state_file(key: str) -> Path:
    return _STATE_DIR / f".rate_limit_{_SAFE_KEY_RE.sub('_', key)}"


def check_and_reserve(key: str, cooldown_seconds: int) -> tuple[bool, float, float]:
    """Atomic (flock-guarded) check-and-set. Returns
    (allowed, seconds_since_last, previous_ts) — previous_ts lets a failed call
    restore the old timestamp via restore() instead of burning the window."""
    f_path = _state_file(key)
    f_path.touch(exist_ok=True)
    with open(f_path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            content = f.read().strip()
            last = float(content) if content else 0.0
            now = time.time()
            elapsed = now - last
            if elapsed < cooldown_seconds:
                return False, elapsed, last
            f.seek(0)
            f.truncate()
            f.write(str(now))
            return True, elapsed, last
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def restore(key: str, previous_ts: float) -> None:
    """Undo the reservation when the guarded action itself failed."""
    with open(_state_file(key), "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            f.write(str(previous_ts))
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def demo():
    key = "test.demo_tool"
    _state_file(key).unlink(missing_ok=True)
    allowed, elapsed, prev = check_and_reserve(key, cooldown_seconds=5)
    assert allowed, "first reservation should be allowed"
    allowed2, elapsed2, prev2 = check_and_reserve(key, cooldown_seconds=5)
    assert not allowed2, "second reservation within cooldown should be refused"
    restore(key, prev2)
    allowed3, _, _ = check_and_reserve(key, cooldown_seconds=0)
    assert allowed3, "restore should allow immediate re-check with 0s cooldown"
    _state_file(key).unlink(missing_ok=True)
    print("rate_limit demo OK")


if __name__ == "__main__":
    demo()
