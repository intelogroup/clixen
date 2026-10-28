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


def test_collapsed_skips_site_chrome_and_stays_inside_main(monkeypatch):
    # Amazon's "Deliver to" header button and footer country picker report expanded=False;
    # pressing them reopens the location popup / navigates away on every page read.
    fake_get(monkeypatch)
    monkeypatch.setattr(ax_context, "_actions", lambda el: ("AXPress",))
    deliver_to = node("AXButton", AXExpanded=False)
    header = node("AXGroup", AXSubrole="AXLandmarkBanner", kids=[deliver_to])
    country = node("AXButton", AXExpanded=False)
    spec = node("AXButton", AXExpanded=False)
    main = node("AXGroup", AXSubrole="AXLandmarkMain", kids=[spec])
    page = node("AXWebArea", kids=[header, main, country])
    assert ax_context._collapsed(page, time.monotonic() + 5) == [spec]
    # No <main>: fall back to the whole page, but still skip the landmarks.
    no_main = node("AXWebArea", kids=[header, node("AXGroup", kids=[spec])])
    assert ax_context._collapsed(no_main, time.monotonic() + 5) == [spec]


def test_passages_returns_matches_with_neighbours_in_page_order():
    lines = ["intro", "Display 10 inch", "Battery 4000 mAh", "Weight 295 g", "footer", "more", "Battery life 14 days"]
    assert passages(lines, "battery") == ["Display 10 inch", "Battery 4000 mAh", "Weight 295 g", "more", "Battery life 14 days"]
    assert passages(lines, "") == lines
    assert passages(lines, "zzz") == []


def test_read_page_text_closes_extension_side_panel_before_reading(monkeypatch):
    from tools import page_text as pt
    calls = []
    monkeypatch.setattr(pt, "_guard", lambda: None)
    monkeypatch.setattr(pt, "close_side_panel", lambda app="": calls.append("close"))
    monkeypatch.setattr(pt, "page_text", lambda app, expand: calls.append("read") or ("T", "u", ["line"], 0))
    assert "line" in pt.read_page_text("", "Google Chrome")
    assert calls == ["close", "read"]
