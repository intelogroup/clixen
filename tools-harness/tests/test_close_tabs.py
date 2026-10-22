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
