"""The dev extension bridge: queue a command, wait for the extension, never hang.

Hermetic — no browser, no Chrome. The live extension is exercised by hand (it needs
a user gesture to grant a tab), so what is tested here is the part that must never
be wrong: a dead extension produces a reason, not a 30 s stall, and a result that
arrives late is not silently dropped on the floor.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import ext_bridge as eb  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    eb.reset_for_tests()
    yield
    eb.reset_for_tests()


def test_not_connected_says_how_to_connect():
    out = eb.probe("console")
    assert out.startswith("[error] not connected")
    assert "load unpacked" in out.lower() or "Load it" in out


def test_command_round_trip():
    eb.mark_seen()
    threading.Timer(0.05, lambda: eb.put_result(
        eb.take()["id"], True, {"entries": [{"type": "error", "text": "boom"}], "count": 1, "errors": 1}
    )).start()
    out = eb.probe("console", timeout=3)
    assert "1 captured, 1 error-level" in out and "error: boom" in out


def test_a_silent_extension_times_out_with_a_reason():
    eb.mark_seen()
    out = eb.probe("dom", timeout=0.6)
    assert "[error]" in out and "no answer" in out


def test_timeout_is_short_by_default():
    assert eb.DEFAULT_WAIT_S <= 10


def test_poll_hands_out_one_command_then_nothing():
    eb.mark_seen()
    eb.enqueue("probe", {"kind": "text"})
    first = eb.take()
    assert first["action"] == "probe" and first["args"]["kind"] == "text"
    assert eb.take() is None


def test_dom_output_is_readable():
    eb.mark_seen()

    def answer():
        cmd = eb.take()
        eb.put_result(cmd["id"], True, {
            "title": "Example", "url": "https://example.com",
            "elements": [{"tag": "h1", "role": "", "label": "Example Domain", "href": ""}],
            "consent": ["Accept all"],
        })

    threading.Timer(0.05, answer).start()
    out = eb.probe("dom", timeout=3)
    assert "Example — https://example.com" in out
    assert "- h1 Example Domain" in out
    assert "consent controls: Accept all" in out


def test_click_reports_what_it_pressed():
    eb.mark_seen()

    def answer():
        cmd = eb.take()
        eb.put_result(cmd["id"], True, {"ok": True, "clicked": "Closer"})

    threading.Timer(0.05, answer).start()
    assert eb.probe("click", text="Closer", timeout=3) == "Clicked 'Closer'"


def test_connection_window_expires():
    eb.mark_seen()
    assert eb.connected()
    eb.reset_for_tests()
    assert not eb.connected()


def test_a_suspended_worker_is_waited_for_not_failed(monkeypatch):
    """Chrome suspends the MV3 worker after ~30 s. Once it has been seen, a call must
    wait out the heartbeat instead of reporting a broken tool."""
    eb.mark_seen()
    eb.reset_for_tests()
    eb._EVER_SEEN = True          # seen before, now asleep
    assert eb.connected() is False
    monkeypatch.setattr(eb, "WAKE_WAIT_S", 1.5)   # do not burn 34 s in a unit test
    start = time.monotonic()
    out = eb.probe("console", timeout=0.2)
    assert "no answer" in out
    assert time.monotonic() - start >= 1.2, "it gave up before the heartbeat could wake it"


def test_never_seen_fails_fast_with_instructions():
    assert "not connected" in eb.probe("console", timeout=0.2)
