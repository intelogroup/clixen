"""Two Chrome instances, and what the orb says about it.

Measured 2026-10-25: this machine runs a personal Chrome and an agent Chrome with its
own --user-data-dir. They share no tabs, and AppleScript addresses only one, so
"opened the page" can be true in a browser the user cannot see. The orb used to get a
vague "not confirmed on screen"; naming the ambiguity is the difference between
retrying blindly and asking a useful question.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import open_actions as oa  # noqa: E402

PS_TWO = """\
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222 --user-data-dir=/Users/x/.chrome-muse-agent
"""

PS_ONE = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n"

PS_SAME_PROFILE = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n"
                   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --restore-last-session\n")


def _fake_ps(monkeypatch, text):
    class R:
        stdout = text

    monkeypatch.setattr(oa.subprocess, "run", lambda *a, **k: R())


def test_two_distinct_profiles_are_reported(monkeypatch):
    _fake_ps(monkeypatch, PS_TWO)
    assert oa._chrome_profiles() == ["(default profile)", "/Users/x/.chrome-muse-agent"]


def test_one_instance_is_not_ambiguous(monkeypatch):
    _fake_ps(monkeypatch, PS_ONE)
    assert oa._chrome_profiles() == []


def test_two_processes_of_the_same_profile_are_not_ambiguous(monkeypatch):
    _fake_ps(monkeypatch, PS_SAME_PROFILE)
    assert oa._chrome_profiles() == []


def test_ps_failure_is_treated_as_no_ambiguity(monkeypatch):
    def boom(*a, **k):
        raise OSError("ps gone")

    monkeypatch.setattr(oa.subprocess, "run", boom)
    assert oa._chrome_profiles() == []


def test_unconfirmed_page_with_two_profiles_names_the_problem(monkeypatch):
    _fake_ps(monkeypatch, PS_TWO)
    monkeypatch.setattr(oa, "_front_url", lambda app: "https://something.else/")
    msg = oa._confirm_visible("Google Chrome", "https://target.test/page", wait_s=0)
    assert "ambiguous_browser" in msg
    assert "other profile" in msg
    assert "Do not retry the same open" in msg


def test_confirmed_page_says_nothing_about_profiles(monkeypatch):
    _fake_ps(monkeypatch, PS_TWO)
    monkeypatch.setattr(oa, "_front_url", lambda app: "https://target.test/page")
    msg = oa._confirm_visible("Google Chrome", "https://target.test/page", wait_s=0.3)
    assert msg == " It is on screen now."


def test_unconfirmed_with_a_single_profile_keeps_the_soft_warning(monkeypatch):
    _fake_ps(monkeypatch, PS_ONE)
    monkeypatch.setattr(oa, "_front_url", lambda app: "https://something.else/")
    msg = oa._confirm_visible("Google Chrome", "https://target.test/page", wait_s=0)
    assert "not confirmed on screen" in msg and "ambiguous_browser" not in msg
