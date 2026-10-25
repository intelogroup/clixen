"""Console capture: the buffer both browser drivers write into.

Before this, every page attached `_page.on("console", lambda _: None)` — the output
was discarded, so "audit this site's console" was impossible from either driver
even though a page object existed.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import console_buffer as cb  # noqa: E402


class _Msg:
    def __init__(self, type_, text, url=""):
        self.type = type_
        self.text = text
        self.location = type("Loc", (), {"url": url})()


@pytest.fixture(autouse=True)
def clean():
    cb.clear()
    yield
    cb.clear()


def test_messages_are_recorded_and_listed():
    cb.record(_Msg("error", "boom", "https://x.test/app.js"))
    cb.record(_Msg("log", "hello"))
    out = cb.messages()
    assert "2 captured" in out
    assert "error: boom  [https://x.test/app.js]" in out
    assert "log: hello" in out


def test_errors_are_counted_as_the_audit_signal():
    cb.record(_Msg("error", "one"))
    cb.record_error(RuntimeError("uncaught"))
    out = cb.messages()
    assert "2 error-level message(s)" in out


def test_level_filter():
    cb.record(_Msg("error", "bad"))
    cb.record(_Msg("warning", "meh"))
    assert "bad" in cb.messages(level="error")
    assert "meh" not in cb.messages(level="error")


def test_empty_buffer_says_how_to_fill_it():
    out = cb.messages()
    assert "No console output captured" in out and "browser_open" in out


def test_ring_is_bounded():
    for i in range(cb._MAX + 25):
        cb.record(_Msg("log", f"line {i}"))
    assert len(cb._ENTRIES) == cb._MAX
    assert "line 0" not in cb.messages(limit=200)


def test_a_broken_message_object_never_raises():
    cb.record(object())  # no .type/.text
    assert "No console output" in cb.messages() or "captured" in cb.messages()
