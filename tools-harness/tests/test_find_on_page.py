"""find_on_page: absence has to be earned by reaching the bottom.

The failure this pins: asked for a January 2025 issue, the orb answered twice that the
page had none. The cards were real; some were below the fold and the rest were behind a
period filter. One look is not evidence.
"""

from __future__ import annotations

import json

import pytest

from tools import screen_v2


class FakePage:
    """A viewport that changes for `pages` scrolls, then stops — like a real page."""

    def __init__(self, viewports):
        self.viewports = viewports
        self.i = 0
        self.scrolls = 0

    def snapshot(self, app="", query=""):
        rows = [{"fp": ("Button", label), "e": {"el": None}} for label in self.viewports[self.i]]
        return "T", [], rows

    def scroll(self, direction, pages, app):
        self.scrolls += 1
        if self.i < len(self.viewports) - 1:
            self.i += 1


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(screen_v2.time, "sleep", lambda *_: None)


def test_finds_a_label_that_is_already_on_screen(no_sleep):
    page = FakePage([["Vol 03 No 44", "Vol 04 No 48"]])
    screen_v2._snapshot = page.snapshot
    screen_v2.screen_act.scroll_page = page.scroll
    out = json.loads(screen_v2.find_on_page("No 44"))
    assert out["found"] is True
    assert out["label"] == "Vol 03 No 44"
    assert page.scrolls == 0, "nothing to scroll for: it was in view"


def test_scrolls_until_it_appears_and_says_how_far(no_sleep, monkeypatch):
    page = FakePage([["2026 issue"], ["2025 banner"], ["Vol 03 No 44"]])
    monkeypatch.setattr(screen_v2, "_snapshot", page.snapshot)
    monkeypatch.setattr(screen_v2.screen_act, "scroll_page", page.scroll)
    out = json.loads(screen_v2.find_on_page("No 44"))
    assert out["found"] is True
    assert page.scrolls == 2
    assert "after scrolling 2 page(s)" in out["note"]


def test_reaching_the_bottom_is_proved_by_the_view_stopping(no_sleep, monkeypatch):
    page = FakePage([["a"], ["b"], ["b"], ["b"]])
    monkeypatch.setattr(screen_v2, "_snapshot", page.snapshot)
    monkeypatch.setattr(screen_v2.screen_act, "scroll_page", page.scroll)
    out = json.loads(screen_v2.find_on_page("nothing-here"))
    assert out["found"] is False
    assert "stopped changing" in out["reason"], out
    assert page.scrolls <= 3, "it must stop at the bottom instead of scrolling forever"


def test_a_bottom_report_names_the_filters_that_could_hide_it(no_sleep, monkeypatch):
    """The actual 2026-10-25 case: the year was behind 'Sélectionner une période'."""
    page = FakePage([
        ["Sélectionner une période", "Catégories"],
        ["Sélectionner une période", "Catégories"],
    ])
    monkeypatch.setattr(screen_v2, "_snapshot", page.snapshot)
    monkeypatch.setattr(screen_v2.screen_act, "scroll_page", page.scroll)
    out = json.loads(screen_v2.find_on_page("janvier 2025"))
    assert out["found"] is False
    labels = [f["label"] for f in out["filters"]]
    assert "Sélectionner une période" in labels
    assert "filter" in out["hint"].lower(), "the next step must be named, not implied"


def test_running_out_of_scrolls_does_not_claim_absence(no_sleep, monkeypatch):
    """Hitting the cap is 'not yet proven', not 'not there'."""
    page = FakePage([[f"page{i}"] for i in range(30)])
    monkeypatch.setattr(screen_v2, "_snapshot", page.snapshot)
    monkeypatch.setattr(screen_v2.screen_act, "scroll_page", page.scroll)
    out = json.loads(screen_v2.find_on_page("x", max_scrolls=2))
    assert out["found"] is False
    assert "more may exist" in out["reason"]
    assert "not on this page" not in json.dumps(out), "hitting the cap is not proof of absence"


def test_the_cap_cannot_be_raised_into_an_infinite_scroll(no_sleep, monkeypatch):
    page = FakePage([[f"page{i}"] for i in range(50)])
    monkeypatch.setattr(screen_v2, "_snapshot", page.snapshot)
    monkeypatch.setattr(screen_v2.screen_act, "scroll_page", page.scroll)
    screen_v2.find_on_page("x", max_scrolls=999)
    assert page.scrolls <= 30


def test_the_orb_can_see_it_exists():
    from tools import lazy_schema
    from tools.registry import ALL_TOOLS
    import orb_agent

    assert "find_on_page" in orb_agent.TOOL_NAMES
    lean = lazy_schema.compress(orb_agent.lean_tools(ALL_TOOLS))
    desc = next(t["function"]["description"] for t in lean if t["function"]["name"] == "find_on_page")
    assert "scroll" in desc.lower()
    assert "reaching the bottom" in desc.lower(), "the truncation must keep the rule, not just the verb"
    assert "not found" in desc.lower() or "not-found" in desc.lower()
