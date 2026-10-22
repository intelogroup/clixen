from collections import deque

from tools import headed_browser as hb


class Resp:
    def __init__(self, body="{}"):
        self._b = body

    def text(self):
        return self._b


class Page:
    def __init__(self, url="https://a.test/", closed=False):
        self.url, self._closed, self.fronted, self.closed_called = url, closed, False, False

    def is_closed(self):
        return self._closed

    def title(self):
        return "T"

    def bring_to_front(self):
        self.fronted = True

    def close(self):
        self._closed = self.closed_called = True


class Ctx:
    def __init__(self, pages):
        self.pages = pages


def _session(monkeypatch, pages, mode="headed (chrome)", rows=()):
    monkeypatch.setattr(hb._S, "page", pages[0], raising=False)
    monkeypatch.setattr(hb._S, "context", Ctx(pages), raising=False)
    monkeypatch.setattr(hb._S, "mode", mode, raising=False)
    monkeypatch.setattr(hb._S, "net", deque(rows, maxlen=200), raising=False)
    monkeypatch.setattr(hb, "_status_line", lambda: "ok")


def row(url, typ="xhr", body="{}"):
    return {"method": "GET", "url": url, "status": 200, "type": typ, "resp": Resp(body)}


def test_network_filters_by_type_and_url_then_reads_body(monkeypatch):
    _session(monkeypatch, [Page()], rows=[row("https://a.test/api/items", body='{"n":1}'), row("https://a.test/x.js", "script"), row("https://a.test/api/user")])
    assert hb._network_inner("", "xhr,fetch", 20, 0).splitlines() == ["#1 GET 200 xhr https://a.test/api/items", "#3 GET 200 xhr https://a.test/api/user"]
    assert hb._network_inner("user", "xhr", 20, 0).startswith("#3")
    assert hb._network_inner("", "", 20, 1).endswith('{"n":1}')
    assert hb._network_inner("", "", 20, 9).startswith("No request #9")


def test_tabs_switch_close_and_fences(monkeypatch):
    a, b = Page("https://a.test/"), Page("https://b.test/")
    _session(monkeypatch, [a, b])
    assert "* T - https://a.test/" in hb._tabs_inner("list", 0, "")
    assert hb._tabs_inner("switch", 2, "").startswith("Switched to tab 2") and hb._S.page is b and b.fronted
    assert hb._tabs_inner("close", 2, "").startswith("Closed tab 2") and b.closed_called and hb._S.page is a
    assert hb._tabs_inner("close", 1, "").startswith("Refused: that is the only tab")
    assert hb._tabs_inner("switch", 7, "").startswith("Tab 7 does not exist")


def test_tabs_close_refused_on_users_own_chrome(monkeypatch):
    a, b = Page(), Page("https://b.test/")
    _session(monkeypatch, [a, b], mode="cdp")
    assert hb._tabs_inner("close", 2, "").startswith("Refused: this is attached to the user's own Chrome") and not b.closed_called

