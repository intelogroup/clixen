"""Consent banners must be reported, and the agent must not elect for the user.

Measured on real sites 2026-10-25: theguardian.com blocked the page behind a
"Privacy Manager" CMP with nothing in the observation payload saying so, and the
CMP's Accept/Reject lived in an iframe (absent from the element list). The US
banner exposed only a neutral "Closer" — no accept, no opt-out button — which is a
different situation from a banner that offers both, and the agent must not treat
them the same: choosing either is a privacy election about the user's data.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import screen_v2 as sv  # noqa: E402


def test_a_plain_page_is_not_flagged():
    assert sv._blocking_banner("", ["Sign in", "Search", "Subscribe"]) == ""


def test_the_guardian_case_reports_the_dismiss_only_banner():
    out = sv._blocking_banner(
        "Privacy Manager US App",
        ["Closer", "Do not sell or share my personal information", "privacy policy"],
    )
    assert "consent banner" in out
    assert "Privacy Manager US App" in out
    # it must name what is available, and never suggest electing for the user
    assert "dismiss: Closer" in out          # the neutral exit is named
    assert "reject: Do not sell" in out       # and so is the election it must not make
    assert "never elect" in out.lower()


def test_accept_and_reject_are_named_but_not_chosen():
    out = sv._blocking_banner("We use cookies", ["Accept all", "Reject all", "Manage choices"])
    assert "accept: Accept all" in out and "reject: Reject all" in out
    assert "user's decision" in out


def test_an_iframe_cmp_with_no_buttons_says_so():
    out = sv._blocking_banner("Privacy Manager", ["Closer"])
    assert "dismiss" in out.lower()


def test_no_button_case_points_at_the_pixel_path():
    out = sv._blocking_banner("consent", ["Some article headline"])
    assert "NOT in the element list" in out and "click_text" in out


def test_consent_words_in_page_text_alone_still_flag_it():
    # A banner can be present while focus sits elsewhere: the words in the labels
    # are enough, because a hidden modal still swallows the click.
    assert sv._blocking_banner("", ["We use cookies to improve your experience"])


def test_the_skill_for_this_exists_and_forbids_electing():
    from tools.skills import load_skill

    doc = load_skill("consent-banners")
    assert "Never choose accept" in doc
    assert "dismiss" in doc.lower()
