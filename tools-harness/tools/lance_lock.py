"""Small inter-process lock for LanceDB mutations."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


@contextlib.contextmanager
def mutation_lock(db_path: str | Path):
    """Serialize a LanceDB write across desktop workers on Unix platforms."""
    lock_path = Path(str(db_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:
            pass
        handle.close()
