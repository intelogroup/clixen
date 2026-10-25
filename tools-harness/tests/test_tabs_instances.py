"""`tabs` must describe the browser the user is actually looking at.

Measured 2026-10-25: two Chrome instances were running — the user's default profile
and an agent profile started with its own `--user-data-dir`. AppleScript addresses
only ONE of them; AX sees whichever window is on screen. `tabs list` returned the
exam tab from the agent profile while the front window was a CNN article in the
other profile, and `tabs close` would have closed a tab in a browser the user could
not see.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import screen_v2  # noqa: E402


def _osascript_rows(*_a, **_k):
    class R:
        stdout = (
            "1|1|Exam | STEP1 | MedicoSpira|https://usmle.example/exam\n"
            "1|2|New Tab|chrome://newtab/\n"
        )
        returncode = 0

    return R()


@pytest.fixture()
def two_instances(monkeypatch):
    monkeypatch.setattr(screen_v2.subprocess, "run", _osascript_rows)
    monkeypatch.setattr(
        screen_v2, "_chrome_instances", lambda: ["/Users/x/.chrome-muse-agent", "/Users/x/Library/…/Chrome"]
    )
    import tools.ax_context as ax

    monkeypatch.setattr(
        ax, "window_title",
        lambda app="": "Police arrest roughly 60 protesters outside US Capitol | CNN Politics - Google Chrome",
    )


def test_list_flags_the_instance_that_is_not_on_screen(two_instances):
    out = screen_v2.tabs(action="list")
    assert '"on_screen": false' in out
    assert "CNN" in out and "profiles are running" in out


def test_switch_and_close_refuse_a_browser_the_user_cannot_see(two_instances):
    out = screen_v2.tabs(action="close", match="Exam")
    assert '"wrong_instance"' in out
    # crucially: no osascript close was issued (the stub would have shown up as a
    # second call with a 'close tab' script, which is why we assert the error code)
    assert '"closed"' not in out


def test_the_matching_instance_is_not_blocked(monkeypatch):
    monkeypatch.setattr(screen_v2.subprocess, "run", _osascript_rows)
    monkeypatch.setattr(screen_v2, "_chrome_instances", lambda: ["/Users/x/Library/…/Chrome"])
    import tools.ax_context as ax

    monkeypatch.setattr(ax, "window_title", lambda app="": "Exam | STEP1 | MedicoSpira - Google Chrome")
    out = screen_v2.tabs(action="list")
    assert '"on_screen"' not in out
    assert "Exam" in out and "New Tab" in out


def test_ax_failure_does_not_break_the_tool(monkeypatch):
    monkeypatch.setattr(screen_v2.subprocess, "run", _osascript_rows)
    import tools.ax_context as ax

    def boom(app=""):
        raise RuntimeError("no AX")

    monkeypatch.setattr(ax, "window_title", boom)
    out = screen_v2.tabs(action="list")
    assert '"ok": true' in out and "Exam" in out
