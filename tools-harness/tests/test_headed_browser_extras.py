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


def test_point_cursor_converts_to_screen_points_and_skips_headless(monkeypatch):
    from tools import orb_cursor

    got = []
    monkeypatch.setattr(orb_cursor, "point", lambda x, y, click=True: got.append((x, y, click)))

    class Loc:
        def bounding_box(self):
            return {"x": 100, "y": 10, "width": 40, "height": 20}

    class P:
        def evaluate(self, js):
            return [22, 34 + 143]  # screenX, screenY + (outerHeight - innerHeight)

    monkeypatch.setattr(hb._S, "page", P(), raising=False)
    monkeypatch.setattr(hb._S, "mode", "headed (chrome)", raising=False)
    hb._point_cursor(Loc())
    assert got == [(142.0, 197.0, True)]
    monkeypatch.setattr(hb._S, "mode", "headless (chrome)", raising=False)
    hb._point_cursor(Loc())
    assert len(got) == 1


def test_click_waits_for_the_bazooka_impact_then_clicks(monkeypatch):
    from tools import orb_cursor

    events = []
    monkeypatch.setattr(orb_cursor, "point", lambda x, y, click=True, style=None: events.append(("point", style)))
    monkeypatch.setattr(hb.time, "sleep", lambda s: events.append(("sleep", round(s, 2))))
    monkeypatch.delenv("CLIXEN_ORB_CLICK_STYLE", raising=False)

    class Loc:
        def scroll_into_view_if_needed(self, timeout=0): pass
        def bounding_box(self): return {"x": 0, "y": 0, "width": 10, "height": 10}
        def click(self, force=False): events.append(("click",))

    class P:
        def evaluate(self, js): return [0, 0]

    monkeypatch.setattr(hb, "_locator_for", lambda ref: (Loc(), ref))
    monkeypatch.setattr(hb, "_settle", lambda: None)
    monkeypatch.setattr(hb, "_status_line", lambda: "ok")
    monkeypatch.setattr(hb._S, "page", P(), raising=False)
    monkeypatch.setattr(hb._S, "mode", "headed (chrome)", raising=False)
    assert hb._click_inner("e1", False).startswith("Clicked @e1")
    assert events == [("point", "bazooka"), ("sleep", 1.6), ("click",)]  # click only after the wait


def test_network_list_waits_for_late_fetches(monkeypatch):
    page = Page()
    _session(monkeypatch, [page])
    page.wait_for_load_state = lambda state, timeout: hb._S.net.append(row("https://a.test/late", "fetch"))
    assert hb._network_inner("", "xhr,fetch", 20, 0) == "#1 GET 200 fetch https://a.test/late"


def test_closing_another_tab_keeps_current_tab(monkeypatch):
    a, b, c = Page("https://a.test/"), Page("https://b.test/"), Page("https://c.test/")
    _session(monkeypatch, [a, b, c])
    hb._S.page = b
    hb._tabs_inner("close", 3, "")
    assert hb._S.page is b
    hb._tabs_inner("close", 2, "")
    assert hb._S.page is a
