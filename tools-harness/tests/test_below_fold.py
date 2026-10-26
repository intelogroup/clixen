"""Below-the-fold elements must read as "scroll to it", never as "not there".

Reported live: the orb tried to fill a form field below the viewport without scrolling,
because `observe` only counted off-screen rows and `act` answered a generic not_found for
them — so the model concluded the field didn't exist instead of scrolling to it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1]
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from tools import screen_v2 as sv  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state():
    sv._state["below"] = []
    yield
    sv._state["below"] = []


def _item(role, label, n=0):
    return {"e": {"role": role, "label": label, "x": 100, "y": 100, "w": 60, "h": 20},
            "fp": (role, label, n)}


def test_a_below_fold_target_is_a_scroll_problem_not_an_absence():
    sv._state["below"] = [("TextField", "Email address")]
    _idx, err = sv._resolve("email", [_item("Link", "Home")])
    assert err is not None
    code, hint, _extra = err
    assert code == "below_viewport"
    assert "Email address" in hint
    assert "find_on_page" in hint, "the hint must name the tool that scrolls it into view"


def test_a_genuinely_absent_target_still_says_not_found():
    sv._state["below"] = [("TextField", "Email address")]
    _idx, err = sv._resolve("unrelated-widget", [_item("Link", "Home")])
    assert err[0] == "not_found"


def test_an_exact_match_below_the_fold_is_detected_too():
    sv._state["below"] = [("Button", "Submit application")]
    _idx, err = sv._resolve("Submit application", [])
    assert err[0] == "below_viewport"


def test_snapshot_records_the_offscreen_rows_by_name(monkeypatch):
    monkeypatch.setattr(sv.ax, "elements", lambda *a, **k: (
        "Google Chrome", "Form", [
            {"role": "TextField", "label": "Name", "onscreen": True, "x": 0, "y": 0, "w": 10, "h": 10, "web": True},
            {"role": "TextField", "label": "Email address", "onscreen": False, "below": True,
             "x": 0, "y": 2000, "w": 10, "h": 0},
        ]))
    _title, _els, _items = sv._snapshot("Google Chrome")
    assert ("TextField", "Email address") in sv._state["below"]


def test_observe_names_the_offscreen_fields_and_how_to_reach_them(monkeypatch):
    monkeypatch.setattr(sv, "_snapshot", lambda app, q: (
        "Form", [
            {"role": "TextField", "label": "Email address", "onscreen": False, "below": True,
             "x": 0, "y": 2000, "w": 10, "h": 0},
        ], []))
    monkeypatch.setattr(sv, "_register", lambda items, title="": 1)
    monkeypatch.setattr(sv, "_focused", lambda: ("", ""))
    monkeypatch.setattr(sv, "_overlays", lambda els, app: [])
    monkeypatch.setattr(sv, "_blocking_banner", lambda label, labels: None)
    monkeypatch.setattr(sv.ax, "front_url", lambda app: "https://form.test/")
    sv._state["below"] = [("TextField", "Email address")]
    out = sv._observe("Google Chrome", "")
    data = json.loads(out)
    assert data["fold"]["below"] == 1
    assert "Email address" in data["fold"]["below_labels"]
    assert "find_on_page" in data["fold"]["hint"]
    assert "outside the viewport" in data["fold"]["hint"]
