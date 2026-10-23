from tools import open_actions as oa


def test_valid_url():
    assert oa.valid_url("https://www.amazon.com")
    assert oa.valid_url("http://localhost:9234/x")
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "amazon.com", "https://", ""):
        assert not oa.valid_url(bad)


def test_refuses_bad_scheme_without_running_open(monkeypatch):
    monkeypatch.setattr(oa, "_open", lambda a: (_ for _ in ()).throw(AssertionError("open called")))
    assert oa.open_url("file:///etc/passwd").startswith("Refused")


def test_unknown_app():
    assert oa.open_app("NoSuchApp12345").startswith("App not found")


def test_open_url_builds_command(monkeypatch):
    calls = []
    monkeypatch.setattr(oa, "_app_installed", lambda n: True)
    monkeypatch.setattr(oa, "_open", lambda a: calls.append(a))
    monkeypatch.setattr(oa, "point_at_app", lambda a: None)
    monkeypatch.setattr(oa, "_reuse_working_tab", lambda app, url: False)
    monkeypatch.setattr(oa, "_tab_list", lambda app: [])
    monkeypatch.setattr(oa, "_remember_working_tab", lambda app, before, url: None)
    assert oa.open_url("https://www.amazon.com").startswith("Opened")
    assert calls == [["-a", "Google Chrome", "https://www.amazon.com"]]


def test_open_url_reuses_the_tab_it_opened_instead_of_opening_another(monkeypatch):
    opened, navigated = [], []
    monkeypatch.setattr(oa, "_app_installed", lambda n: True)
    monkeypatch.setattr(oa, "_front_url", lambda app: "https://other.example")
    monkeypatch.setattr(oa, "_open", lambda a: opened.append(a))
    monkeypatch.setattr(oa, "point_at_app", lambda a: None)
    monkeypatch.setattr(oa, "_reuse_working_tab", lambda app, url: navigated.append(url) or True)
    assert "reusing the tab" in oa.open_url("https://www.amazon.com")
    assert navigated == ["https://www.amazon.com"] and opened == []


def test_reuse_drops_a_closed_tab_and_falls_back_to_a_new_one(monkeypatch):
    tab = object()
    oa._work_tab[(7, "example.com")] = tab
    monkeypatch.setattr(oa, "_pid", lambda app: 7)
    monkeypatch.setattr(oa, "_tab_list", lambda app: [object()])  # our tab is gone from the strip
    assert oa._reuse_working_tab("Google Chrome", "https://example.com") is False
    assert (7, "example.com") not in oa._work_tab


def test_orb_cursor_point_roundtrip(tmp_path, monkeypatch):
    import json

    from tools import orb_cursor as oc

    monkeypatch.setattr(oc, "_FILE", tmp_path / "c.json")
    oc.point(10.5, 20, click=True)
    d = json.loads((tmp_path / "c.json").read_text())
    assert (d["x"], d["y"], d["click"]) == (10.5, 20, True) and d["seq"] and d["ts"]


def test_each_site_keeps_its_own_tab(monkeypatch):
    youtube, wiki = object(), object()
    oa._work_tab.clear()
    oa._work_tab[(7, "youtube.com")] = youtube
    oa._work_tab[(7, "wikipedia.org")] = wiki
    pressed = []
    monkeypatch.setattr(oa, "_pid", lambda app: 7)
    monkeypatch.setattr(oa, "_tab_list", lambda app: [youtube, wiki])
    import tools.ax_context as ax
    monkeypatch.setattr(ax, "select_tab", lambda t: pressed.append(t) or True)
    monkeypatch.setattr(ax, "activate_app", lambda app: True)
    monkeypatch.setattr(oa.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    monkeypatch.setattr(oa.time, "sleep", lambda s: None)
    assert oa._reuse_working_tab("Google Chrome", "https://en.wikipedia.org/wiki/Ada_Lovelace")
    assert pressed == [wiki]
    assert oa._reuse_working_tab("Google Chrome", "https://www.youtube.com/results?search_query=x")
    assert pressed == [wiki, youtube]
    assert oa._reuse_working_tab("Google Chrome", "https://www.rust-lang.org") is False  # new site: caller opens a new tab
    assert oa.site_of("https://www.youtube.com/watch?v=1") == "youtube.com"
