"""Safari and Chrome through the same `tabs` tool: one layer, two AppleScript dialects.

Safari exposes `name` where Chrome exposes `title`, and switches with
`current tab of window` instead of `active tab index`. Getting that wrong fails
silently (an empty or malformed list), so the script builders are pinned here
rather than discovered at runtime.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import screen_v2  # noqa: E402


def test_browser_families():
    assert screen_v2._family("Safari") == "safari"
    assert screen_v2._family("Google Chrome") == "chrome"
    assert screen_v2._family("Brave Browser") == "chrome"
    assert screen_v2._family("Firefox") == ""


def test_safari_speaks_name_not_title():
    script = screen_v2._list_script("Safari", "safari")
    assert "name of tab" in script and "title of tab" not in script
    assert "URL of tab" in script  # same property in both dialects


def test_chrome_keeps_title():
    script = screen_v2._list_script("Google Chrome", "chrome")
    assert "title of tab" in script and "name of tab" not in script


def test_safari_switches_with_current_tab():
    script = screen_v2._switch_script("Safari", "safari", 2, 3)
    assert "set current tab of window 2 to tab 3 of window 2" in script
    assert "active tab index" not in script


def test_chrome_switches_with_active_tab_index():
    script = screen_v2._switch_script("Google Chrome", "chrome", 1, 2)
    assert "set active tab index of window 1 to 2" in script


def test_an_unsupported_browser_says_what_is_supported():
    out = screen_v2.tabs(action="list", app="Firefox")
    assert '"unsupported_app"' in out and "Safari" in out


@pytest.fixture()
def safari_rows(monkeypatch):
    class R:
        stdout = "1|1|Example Domain|https://example.com/\n1|2|Second|https://example.org/\n"
        returncode = 0

    monkeypatch.setattr(screen_v2.subprocess, "run", lambda *_a, **_k: R())
    import tools.ax_context as ax

    monkeypatch.setattr(ax, "window_title", lambda app="": "Example Domain")
    return R


def test_safari_list_end_to_end(safari_rows):
    out = screen_v2.tabs(action="list", app="Safari")
    assert '"ok": true' in out
    assert "Example Domain" in out and "https://example.com/" in out
    assert '"on_screen"' not in out  # the front window matches: no warning


def test_safari_switch_uses_its_own_dialect(safari_rows):
    seen = []
    original = screen_v2.subprocess.run
    screen_v2.subprocess.run = lambda argv, **kw: (seen.append(argv[-1]), original(argv, **kw))[1]
    try:
        out = screen_v2.tabs(action="switch", match="Second", app="Safari")
    finally:
        screen_v2.subprocess.run = original
    assert '"switched_to"' in out
    assert any("set current tab of window 1 to tab 2" in s for s in seen)


# ── front_url fallback ───────────────────────────────────────────────────────
# Safari's AX tree carries no AXURL, so "is this page on screen?" answered No for
# every Safari page until the AppleScript fallback existed (verified live).


def test_safari_front_url_falls_back_to_applescript(monkeypatch):
    from tools import ax_context as ax

    class R:
        stdout = "https://example.org/\n"

    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    out = ax._applescript_front_url("Safari")
    assert out == "https://example.org/"


def test_chrome_is_never_read_through_applescript():
    """The two-instance lesson: AppleScript may describe a browser the user cannot see."""
    from tools import ax_context as ax

    assert "google chrome" not in ax._APPLESCRIPT_URL
    assert ax._applescript_front_url("Google Chrome") == ""
