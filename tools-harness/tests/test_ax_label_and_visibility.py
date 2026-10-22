from tools import ax_context


def test_label_falls_back_to_placeholder(monkeypatch):
    attrs = {"AXTitle": "", "AXDescription": "", "AXPlaceholderValue": "Search or ask a question", "AXValue": ""}
    monkeypatch.setattr(ax_context, "_get", lambda el, a: attrs.get(a))
    assert ax_context._label(object()) == "Search or ask a question"


def _centre(monkeypatch, full, win, strict):
    # no AXParent: only the window cut applies
    monkeypatch.setattr(ax_context, "_frame", lambda el: full)
    monkeypatch.setattr(ax_context, "_get", lambda el, a: None)
    return ax_context._visible_centre(object(), win, strict=strict)


def test_sliver_under_a_sticky_header_is_not_clickable_when_strict(monkeypatch):
    win = (0.0, 100.0, 800.0, 600.0)
    sliver = (50.0, 81.0, 100.0, 20.0)  # 19px of 20 hidden above the window top
    assert _centre(monkeypatch, sliver, win, strict=True) is None
    assert _centre(monkeypatch, sliver, win, strict=False) is not None  # last resort after scrolling


def test_fully_visible_element_is_clickable_when_strict(monkeypatch):
    win = (0.0, 100.0, 800.0, 600.0)
    assert _centre(monkeypatch, (50.0, 300.0, 100.0, 20.0), win, strict=True) == (100.0, 310.0)
