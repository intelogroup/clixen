import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_tree(tmp: Path):
    (tmp / "one.m4a").write_text("x")
    (tmp / "two.mp3").write_text("x")
    (tmp / "note.txt").write_text("x")


def test_fd_find_glob_pattern(tmp_path):
    """fd_find must treat glob patterns as globs (regression: default regex
    mode made '*.m4a' a regex-parse error -> silent empty result)."""
    _make_tree(tmp_path)
    from tools.registry import execute_tool
    out = execute_tool("fd_find", {"pattern": "*.m4a", "path": str(tmp_path)})
    assert "one.m4a" in out and "two.mp3" not in out


def test_fd_find_brace_pattern(tmp_path):
    """Brace expansion ({a,b}) must work — regex mode + fnmatch fallback both
    lack it, so searches used to return nothing."""
    _make_tree(tmp_path)
    from tools.registry import execute_tool
    out = execute_tool("fd_find", {"pattern": "*.{m4a,mp3}", "path": str(tmp_path)})
    assert "one.m4a" in out and "two.mp3" in out and "note.txt" not in out


def test_fd_find_fallback_logs_reason(tmp_path, caplog):
    """Silent degradation masked the regex-mode bug — fd errors must be logged."""
    _make_tree(tmp_path)
    import logging
    from tools import fast_search
    from tools.registry import execute_tool
    with caplog.at_level(logging.WARNING, logger="fast_search"):
        # unterminated brace = glob parse error -> fd fails -> fallback logs
        out = execute_tool("fd_find", {"pattern": "*.{m4a", "path": str(tmp_path)})
    assert any("fd failed" in r.message for r in caplog.records)
