"""
Shared logging configuration for gemma4llama.

Usage:
    from log_config import setup_logging

    logger = setup_logging("my_module", log_file="my_module.log")

All modules importing this get consistent format, rotation, and env-var level control.
"""

import contextvars
import logging
import logging.handlers
import os
from pathlib import Path

_LOG_DIR = Path(__file__).parent

# ContextVar holding the active run_id to correlate logs.
# Set this at the start of a request flow using CURRENT_RUN_ID.set(run_id).
CURRENT_RUN_ID: contextvars.ContextVar[str] = contextvars.ContextVar("current_run_id", default="")


class RunIdFilter(logging.Filter):
    """Filter that injects current_run_id ContextVar value as record.run_id."""

    def filter(self, record):
        # Default to a placeholder if no run_id is active
        record.run_id = CURRENT_RUN_ID.get() or "--------"
        return True


def setup_logging(
    name: str,
    log_file: str | None = None,
    default_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    log = logging.getLogger(name)

    if log.handlers:
        return log

    # Some tests import production modules (e.g. telegram_bot.py) purely for their
    # helper functions, which triggers this file's real log_file as an import
    # side-effect — and since this function also wires the *root* logger to that
    # file (below), every other test's log output then bleeds into the real
    # production log, making test noise look like a live incident. Route to
    # stream-only under pytest instead. Checking sys.modules (not
    # PYTEST_CURRENT_TEST) because module imports happen at collection time,
    # before pytest sets that env var for an individual test.
    import sys
    if "pytest" in sys.modules:
        log_file = None

    level_name = os.environ.get("G4L_LOG_LEVEL", "").upper()
    level = getattr(logging, level_name, default_level) if level_name else default_level
    log.setLevel(level)

    # Format includes [%(run_id)s] for correlation
    fmt = "%(asctime)s [%(levelname)s] [%(run_id)s] %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)
    run_filter = RunIdFilter()

    stream = logging.StreamHandler()
    stream.setLevel(level)
    stream.setFormatter(formatter)
    stream.addFilter(run_filter)
    log.addHandler(stream)

    if log_file:
        path = _LOG_DIR / log_file
        fh = logging.handlers.RotatingFileHandler(
            str(path), maxBytes=max_bytes, backupCount=backup_count
        )
        fh.setLevel(level)
        fh.setFormatter(formatter)
        fh.addFilter(run_filter)
        log.addHandler(fh)

    # Wire root logger so child modules (clients.*, harness, agents.*) propagate.
    # Prevents silent log drops from modules that use logging.getLogger(__name__).
    _root = logging.getLogger()
    _root.setLevel(logging.DEBUG)

    # Check if a non-file StreamHandler is already added
    has_stream = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in _root.handlers
    )
    if not has_stream:
        _root.addHandler(stream)

    if log_file:
        target_abs = os.path.abspath(str(_LOG_DIR / log_file))
        has_file = any(
            isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == target_abs
            for h in _root.handlers
        )
        if not has_file:
            _root.addHandler(fh)

    # Prevent double-printing — the named logger's own handlers already fire
    log.propagate = False

    return log
