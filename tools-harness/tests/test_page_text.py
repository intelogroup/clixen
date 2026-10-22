import time

from tools import ax_context
from tools.page_text import passages


def node(role, value=None, kids=(), **attrs):
    return {"AXRole": role, "AXValue": value, "AXChildren": list(kids), **attrs}


def fake_get(monkeypatch):
    monkeypatch.setattr(ax_context, "_get", lambda el, a: el.get(a))


def test_walk_text_reads_blocks_headings_and_offscreen_text(monkeypatch):
    fake_get(monkeypatch)
    page = node("AXWebArea", kids=[
        node("AXHeading", kids=[node("AXStaticText", "Specs")]),
        node("AXGroup", kids=[node("AXStaticText", "Battery "), node("AXStaticText", "4000 mAh")]),
        node("AXGroup", kids=[node("AXStaticText", "Weight 295 g")]),
    ])
    assert ax_context._walk_text(page, time.monotonic() + 5) == ["# Specs", "Battery 4000 mAh", "Weight 295 g"]


def test_walk_text_skips_empty_and_repeated_lines(monkeypatch):
    fake_get(monkeypatch)
    page = node("AXWebArea", kids=[
        node("AXGroup", kids=[node("AXStaticText", "  ")]),
        node("AXGroup", kids=[node("AXStaticText", "same")]),
        node("AXGroup", kids=[node("AXStaticText", "same")]),
    ])
    assert ax_context._walk_text(page, time.monotonic() + 5) == ["same"]


def test_collapsed_finds_expanders_but_never_popup_openers(monkeypatch):
    fake_get(monkeypatch)
    monkeypatch.setattr(ax_context, "_actions", lambda el: ("AXPress",))
    spec = node("AXButton", AXExpanded=False)
    menu = node("AXButton", AXExpanded=False, AXHasPopup=True)  # a dropdown: opening it is a side effect
    done = node("AXButton", AXExpanded=True)
    page = node("AXWebArea", kids=[spec, menu, done])
    assert ax_context._collapsed(page, time.monotonic() + 5) == [spec]


def test_passages_returns_matches_with_neighbours_in_page_order():
    lines = ["intro", "Display 10 inch", "Battery 4000 mAh", "Weight 295 g", "footer", "more", "Battery life 14 days"]
    assert passages(lines, "battery") == ["Display 10 inch", "Battery 4000 mAh", "Weight 295 g", "more", "Battery life 14 days"]
    assert passages(lines, "") == lines
    assert passages(lines, "zzz") == []
