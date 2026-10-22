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
