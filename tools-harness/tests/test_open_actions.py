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
    assert oa.open_url("https://www.amazon.com").startswith("Opened")
    assert calls == [["-a", "Google Chrome", "https://www.amazon.com"]]


def test_orb_cursor_point_roundtrip(tmp_path, monkeypatch):
    import json

    from tools import orb_cursor as oc

    monkeypatch.setattr(oc, "_FILE", tmp_path / "c.json")
    oc.point(10.5, 20, click=True)
    d = json.loads((tmp_path / "c.json").read_text())
    assert (d["x"], d["y"], d["click"]) == (10.5, 20, True) and d["seq"] and d["ts"]
