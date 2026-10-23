from tools import screen_act


def test_terminal_is_never_closed(monkeypatch):
    monkeypatch.setattr(screen_act, "_guard", lambda typing=False: None)
    assert "terminal" in screen_act.close_tabs("Terminal")


def test_unrunning_app_is_reported(monkeypatch):
    monkeypatch.setattr(screen_act, "_guard", lambda typing=False: None)
    assert "not running" in screen_act.close_tabs("No Such Browser 9")


def test_registered_and_offered_on_the_orb_path():
    from tools.registry import EXECUTORS
    import harness

    assert "close_tabs" in EXECUTORS
    assert "close_tabs" in open(harness.__file__).read()


def test_key_script_understands_shortcuts():
    ks = screen_act.key_script
    assert ks("cmd+d") == 'tell application "System Events" to keystroke "d" using {command down}'
    assert ks("cmd+shift+t").endswith("using {command down, shift down}")
    assert ks("esc").endswith("key code 53")
    assert '"="' in ks("cmd+plus") and ks("cmd++") == ks("cmd+plus")  # unshifted plus key
    assert ks("hyper+x") is None and ks("cmd+notakey") is None and ks("") is None


def test_press_keys_refuses_a_terminal_front_window(monkeypatch):
    monkeypatch.setattr(screen_act, "_front_window", lambda: ("Terminal", "zsh"))
    assert "terminal" in screen_act.press_keys("cmd+d")


def test_click_text_refuses_an_ambiguous_label(monkeypatch):
    from tools import ax_context

    monkeypatch.setattr(screen_act, "_guard", lambda typing=False: None)
    def boom(t, app=""):
        raise ax_context.AmbiguousTarget(["Add to cart Kettle", "Add to cart Mug"])
    monkeypatch.setattr(screen_act, "find_element", boom)
    r = screen_act.click_text("Add to cart")
    assert "2 different elements" in r and "Kettle" in r


def test_side_panel_and_extensions_are_reported():
    from tools import ax_context

    els = [{"role": "PopUpButton", "label": "Monica: All-In-One AI Assist Has access to this site"},
           {"role": "PopUpButton", "label": "Thunderbit: AI Web Scraping Agent Has access to this site"},
           {"role": "PopUpButton", "label": "Extensions"}, {"role": "Button", "label": "Close Side Panel"}]
    assert ax_context.toolbar_extensions(els) == ["Monica", "Thunderbit"]
    assert ax_context.side_panel_open(els) and not ax_context.side_panel_open(els[:3])


def _stub_ax(monkeypatch, els):
    from tools import ax_context

    monkeypatch.setattr(ax_context, "_target_app", lambda app="": (1, "Google Chrome"))
    monkeypatch.setattr(ax_context, "elements", lambda app, label, offscreen=False: ("Google Chrome", "t", els))
    monkeypatch.setattr(ax_context, "_ax_app", lambda pid: None)
    monkeypatch.setattr(ax_context, "_get", lambda el, attr: None)
    monkeypatch.setattr(ax_context, "_visible_centre", lambda el, rect, strict=False: (el, 0))
    return ax_context


def test_find_element_prefers_the_page_over_browser_chrome(monkeypatch):
    ax = _stub_ax(monkeypatch, [
        {"role": "RadioButton", "label": "Node.js - Download Node.js - Memory usage - 200 MB", "el": "tab", "web": False},
        {"role": "Link", "label": "Download", "el": "page", "web": True},
    ])
    assert ax.find_element("Download") == ("page", 0)


def test_find_element_still_reaches_browser_chrome_when_the_page_has_no_match(monkeypatch):
    ax = _stub_ax(monkeypatch, [{"role": "RadioButton", "label": "Gmail - Inbox", "el": "tab", "web": False}])
    assert ax.find_element("Gmail") == ("tab", 0)


def test_ambiguous_target_lists_short_control_labels_before_long_inbox_rows(monkeypatch):
    import pytest

    rows = [{"role": "Row", "label": f"{i} new notifications since 5:06 am - long inbox row subject text", "el": i, "web": True} for i in range(12)]
    ax = _stub_ax(monkeypatch, rows + [{"role": "TextField", "label": "To recipients", "el": "to", "web": True}])
    with pytest.raises(ax.AmbiguousTarget) as e:
        ax.find_element("To")
    assert e.value.args[0][0] == "To recipients"  # click_text shows only the first 8 names to the agent


def test_click_text_opens_a_folded_menu_to_reach_the_target(monkeypatch):
    clicks = []
    state = {"open": False}
    monkeypatch.setattr(screen_act, "_guard", lambda typing=False: None)
    monkeypatch.setattr(screen_act, "labels_at", lambda x, y: ["download"])
    monkeypatch.setattr(screen_act, "_click", lambda x, y: (clicks.append((x, y)), state.update(open=not state["open"])))
    monkeypatch.setattr(screen_act, "window_title", lambda app="": "t")
    monkeypatch.setattr(screen_act, "settle_title", lambda before, app="": before)
    monkeypatch.setattr(screen_act, "elements", lambda app="": ("c", "t", [{"role": "Button", "label": "Toggle navigation menu", "web": True}]))
    monkeypatch.setattr(screen_act, "find_element", lambda label, app="": (5, 5) if label.startswith("Toggle") else ((9, 9) if state["open"] else None))
    monkeypatch.setattr(screen_act, "_ocr_boxes", lambda accurate=False: [])
    monkeypatch.setattr(screen_act, "time", type("T", (), {"sleep": staticmethod(lambda s: None), "monotonic": staticmethod(lambda: 0.0)}))
    out = screen_act.click_text("Download")
    assert "opened the 'Toggle navigation menu' menu first" in out and clicks == [(5, 5), (9, 9)]
