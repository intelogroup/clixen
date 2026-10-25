"""The last-look guard: input tools must not act on a screen that moved.

`act()` is anchored by element ids and rejects a stale one. `click_text()` and
`type_text()` are not: they work by label or by keystroke on whatever is
frontmost *now*. Look at Chrome, switch to Terminal, type — the keystrokes land
in the terminal. screen_epoch records the app from the last screen read so those
two can refuse instead.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import screen_epoch  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_epoch():
    screen_epoch.reset()
    yield
    screen_epoch.reset()


def test_without_a_look_it_does_not_block():
    """No record means no worse than before the guard existed."""
    assert screen_epoch.changed_since_look("Google Chrome", "Inbox") is None


def test_the_same_app_is_fine():
    screen_epoch.note_look("Google Chrome", "Inbox")
    assert screen_epoch.changed_since_look("Google Chrome", "Inbox") is None
    assert screen_epoch.changed_since_look("google chrome", "other title") is None


def test_a_different_app_is_refused_and_says_what_changed():
    screen_epoch.note_look("Google Chrome", "Inbox")
    why = screen_epoch.changed_since_look("Ghostty", "zsh")
    assert why and "Ghostty" in why and "Google Chrome" in why
    assert "look again" in why.lower(), "the message has to say what to do next"


def test_an_old_look_stops_vouching_for_the_screen():
    screen_epoch.note_look("Google Chrome", "Inbox")
    assert screen_epoch.changed_since_look("Ghostty", "zsh", ttl=-1) is None


def test_the_description_names_the_title_when_there_is_one():
    screen_epoch.note_look("Google Chrome", "Inbox (3)")
    assert 'Google Chrome ("Inbox (3)")' == screen_epoch.described()
    screen_epoch.reset()
    assert screen_epoch.described() == ""


def test_guard_refuses_when_another_app_came_to_the_front(monkeypatch):
    """The whole point: type_text on an implicit front app, after looking elsewhere."""
    pytest.importorskip("AppKit")  # screen_act pulls the accessibility stack
    from tools import screen_act

    screen_epoch.note_look("Google Chrome", "Inbox")
    monkeypatch.setattr(screen_act, "_front_window", lambda: ("Ghostty", "zsh"))
    monkeypatch.setattr(screen_act, "_screen_locked", lambda: False)
    monkeypatch.setattr(screen_act, "is_blocked", lambda a, t: False)

    why = screen_act._guard()
    assert why and "Ghostty" in why


def test_guard_allows_an_explicitly_named_app(monkeypatch):
    """click_text('Send', app='Chrome') declares its target: that is the intent,
    so a stale look must not veto it."""
    pytest.importorskip("AppKit")
    from tools import screen_act

    screen_epoch.note_look("Ghostty", "zsh")
    monkeypatch.setattr(screen_act, "_front_window", lambda: ("Google Chrome", "Inbox"))
    monkeypatch.setattr(screen_act, "_screen_locked", lambda: False)
    monkeypatch.setattr(screen_act, "is_blocked", lambda a, t: False)

    assert screen_act._guard(acting_on="Google Chrome") is None
