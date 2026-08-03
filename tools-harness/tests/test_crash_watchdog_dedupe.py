"""
Regression test for the 2026-07-11 duplicate-Telegram-alert bug:
log_config.py's setup_logging() wires every module's file handler onto the
shared root logger, so in core.py's single multi-threaded process one crash
gets logged into task_worker.log/chat_ui.log/core_stderr.log simultaneously
— crash_watchdog.py's old per-file debounce meant one real crash produced
3-4 separate Telegram alerts. Fixed by deduping on a content hash instead of
file path.
"""
from __future__ import annotations

from pathlib import Path

from crash_watchdog import _crash_key


def test_same_crash_content_same_key_regardless_of_whitespace():
    line = "Traceback (most recent call last)"
    snippet_a = "  IndexError: index 510 is out of bounds for axis 0 with size 510  "
    snippet_b = "IndexError: index 510 is out of bounds for axis 0 with size 510"
    assert _crash_key(line, snippet_a) == _crash_key(line, snippet_b)


def test_different_crash_content_different_key():
    line = "Traceback (most recent call last)"
    key_a = _crash_key(line, "IndexError: index 510 is out of bounds for axis 0 with size 510")
    key_b = _crash_key(line, "ModuleNotFoundError: No module named 'resemblyzer'")
    assert key_a != key_b


def test_dedupe_suppresses_second_alert_within_window(monkeypatch, tmp_path):
    import crash_watchdog as cw

    sent = []
    monkeypatch.setattr(cw, "send_telegram", lambda msg: sent.append(msg))

    log_a = tmp_path / "task_worker.log"
    log_b = tmp_path / "chat_ui.log"
    # Same crash record fanned out to two files (log_config.py's root-logger
    # wiring) — timestamps a second apart, as they'd realistically be.
    crash_a = "2026-07-11 19:11:42,100 [ERROR] test: Traceback (most recent call last)\nIndexError: index 510 is out of bounds for axis 0 with size 510\n"
    crash_b = "2026-07-11 19:11:42,873 [ERROR] test: Traceback (most recent call last)\nIndexError: index 510 is out of bounds for axis 0 with size 510\n"

    last_alert: dict[str, float] = {}
    assert cw.check_new_text(log_a, crash_a, last_alert) is True
    assert cw.check_new_text(log_b, crash_b, last_alert) is False, "differing timestamps should not defeat the dedupe key"

    assert len(sent) == 1, f"expected exactly 1 alert for the same crash in 2 files, got {len(sent)}"


def test_different_crashes_both_alert(monkeypatch):
    import crash_watchdog as cw

    sent = []
    monkeypatch.setattr(cw, "send_telegram", lambda msg: sent.append(msg))

    crash_a = "Traceback (most recent call last)\nIndexError: index 510 is out of bounds for axis 0 with size 510\n"
    crash_b = "Traceback (most recent call last)\nModuleNotFoundError: No module named 'resemblyzer'\n"

    last_alert: dict[str, float] = {}
    assert cw.check_new_text(Path("a.log"), crash_a, last_alert) is True
    assert cw.check_new_text(Path("b.log"), crash_b, last_alert) is True
    assert len(sent) == 2
