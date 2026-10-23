import json

import pytest

from tools import screen_v2 as v2


def _el(role, label, x=10, y=10, web=True):
    return {"role": role, "label": label, "x": x, "y": y, "w": 50, "h": 20, "el": object(), "web": web}


@pytest.fixture
def screen(monkeypatch):
    """A fake screen: tests set `s.els` (what the accessibility tree shows now) and read `s.clicks`."""
    class S:
        els = []
        clicks = []
        title = "Page"
        focus = ("", "")
        typed = []
        panel_closed = 0

    s = S()
    monkeypatch.setattr(v2.ax, "elements", lambda app="", query="", offscreen=False: ("Google Chrome", s.title, list(s.els)))
    monkeypatch.setattr(v2.ax, "popovers", lambda app="": [])
    monkeypatch.setattr(v2.ax, "front_url", lambda app="": "https://x.test/")
    monkeypatch.setattr(v2.ax, "window_title", lambda app="": s.title)
    monkeypatch.setattr(v2, "_focused", lambda: s.focus)
    monkeypatch.setattr(v2, "_value", lambda e: "")
    monkeypatch.setattr(v2.screen_act, "_guard", lambda typing=False: None)
    monkeypatch.setattr(v2.screen_act, "_click", lambda x, y: s.clicks.append((x, y)))
    monkeypatch.setattr(v2.screen_act, "type_text", lambda text, submit=False: s.typed.append(text) or f"Typed {len(text)} chars.")
    monkeypatch.setattr(v2.screen_act, "close_side_panel", lambda app="": setattr(s, "panel_closed", s.panel_closed + 1) or True)
    monkeypatch.setattr(v2.time, "sleep", lambda t: None)
    v2._state.update(rev=0, items={})
    return s


def _j(out):
    return json.loads(out)


def test_observe_numbers_page_elements_first_and_caps_the_list(screen):
    screen.els = [_el("Button", "Toolbar thing", y=1, web=False)] + [_el("Link", f"Link {i}", y=10 + i) for i in range(60)]
    o = _j(v2.observe())
    assert o["rev"] == 1 and o["app"] == "Google Chrome" and o["url"] == "https://x.test/"
    assert len(o["elements"]) == v2.MAX_ITEMS
    assert o["elements"][0]["id"] == "e1.1" and o["elements"][0]["label"] == "Link 0"  # page content before browser chrome
    assert all(len(e["label"]) <= 80 for e in o["elements"])


def test_observe_reports_overlays(screen, monkeypatch):
    screen.els = [_el("Button", "Close Side Panel", web=False), _el("Link", "Home")]
    monkeypatch.setattr(v2.ax, "popovers", lambda app="": ["Monica sign in"])
    o = _j(v2.observe())
    assert "extension side panel" in o["overlays"] and any("Monica sign in" in x for x in o["overlays"])


def test_act_click_by_id_reports_what_appeared(screen, monkeypatch):
    screen.els = [_el("Button", "Send", 100, 200)]
    _j(v2.observe())
    after = [_el("Button", "Send", 100, 200), _el("StaticText", "Message sent")]

    def clicked(x, y):
        screen.clicks.append((x, y))
        screen.els = after

    monkeypatch.setattr(v2.screen_act, "_click", clicked)
    r = _j(v2.act("e1.1", "click"))
    assert r["ok"] and screen.clicks == [(100, 200)] and "Message sent" in r["effect"]["appeared"]


def test_act_ambiguous_label_lists_candidates_with_ids(screen):
    screen.els = [_el("Button", "To", 1, 1), _el("TextField", "To recipients", 2, 2), _el("Button", "To", 3, 3)]
    r = _j(v2.act("To", "click"))
    assert not r["ok"] and r["error"]["code"] == "ambiguous" and len(r["error"]["candidates"]) == 2
    assert screen.clicks == []


def test_act_not_found_suggests_close_labels(screen):
    screen.els = [_el("TextField", "Subject")]
    r = _j(v2.act("Subjct line", "click"))
    assert r["error"]["code"] == "not_found" and "Subject" in json.dumps(r["error"]["candidates"])


def test_act_stale_id_is_rejected_not_clicked(screen):
    screen.els = [_el("Button", "Send")]
    _j(v2.observe())
    screen.els = [_el("Button", "Discard")]  # the page changed under the id
    r = _j(v2.act("e1.1", "click"))
    assert r["error"]["code"] == "stale" and screen.clicks == []


def test_act_type_needs_an_editable_target(screen):
    screen.els = [_el("Button", "Send")]
    r = _j(v2.act("Send", "type", text="hello"))
    assert r["error"]["code"] == "not_editable" and screen.typed == []


def test_act_type_focuses_then_types_and_reports_the_value(screen):
    screen.els = [_el("TextField", "Subject", 5, 5)]
    screen.focus = ("TextField", "Subject")
    r = _j(v2.act("Subject", "type", text="Hello"))
    assert r["ok"] and screen.clicks == [(5, 5)] and screen.typed == ["Hello"]


def test_act_expect_waits_for_a_late_element(screen):
    screen.els = [_el("Button", "Send")]
    seen = {"n": 0}
    real = v2.ax.elements

    def slow(app="", query="", offscreen=False):
        seen["n"] += 1
        extra = [_el("StaticText", "Message sent")] if seen["n"] >= 5 else []
        return "Google Chrome", "Page", screen.els + extra

    v2.ax.elements = slow
    try:
        r = _j(v2.act("Send", "click", expect={"appears": "Message sent"}))
    finally:
        v2.ax.elements = real
    assert r["ok"] and r["expect"] == "met"


def test_act_expect_unmet_is_a_typed_failure(screen):
    screen.els = [_el("Button", "Send")]
    r = _j(v2.act("Send", "click", expect={"appears": "Message sent"}))
    assert not r["ok"] and r["error"]["code"] == "expect_failed"


def test_act_closes_an_extension_side_panel_first_and_says_so(screen):
    screen.els = [_el("Button", "Close Side Panel", web=False), _el("Button", "Send")]
    r = _j(v2.act("Send", "click"))
    assert screen.panel_closed == 1 and "extension side panel" in r["cleared"]


def test_act_click_with_no_visible_change_warns(screen):
    screen.els = [_el("Button", "Send")]
    r = _j(v2.act("Send", "click"))
    assert r["ok"] and r["warning"] == "no_effect"


def test_act_refused_by_the_guard_is_typed(screen, monkeypatch):
    monkeypatch.setattr(v2.screen_act, "_guard", lambda typing=False: "[screen error] Screen is locked.")
    screen.els = [_el("Button", "Send")]
    r = _j(v2.act("Send", "click"))
    assert r["error"]["code"] == "refused"


def test_tools_are_wired_for_the_agent():
    import orb_agent
    from tools.registry import ALL_TOOLS, EXECUTORS

    for n in ("observe", "act"):
        assert n in EXECUTORS and n in orb_agent.TOOL_NAMES
        assert any(t["function"]["name"] == n for t in ALL_TOOLS)


def _count_walks(monkeypatch, screen):
    walks = {"n": 0}
    orig = v2.ax.elements

    def counted(app="", query="", offscreen=False):
        walks["n"] += 1
        return orig(app, query, offscreen)

    monkeypatch.setattr(v2.ax, "elements", counted)
    return walks


def test_act_by_a_fresh_id_skips_the_pre_walk_when_the_point_still_holds_the_label(screen, monkeypatch):
    screen.els = [_el("Button", "Send", 100, 200)]
    _j(v2.observe())
    walks = _count_walks(monkeypatch, screen)
    monkeypatch.setattr(v2.ax, "labels_at", lambda x, y: ["Send"])
    r = _j(v2.act("e1.1", "click"))
    assert r["ok"] and screen.clicks == [(100, 200)] and walks["n"] == 1  # only the after-walk


def test_act_by_id_walks_again_when_the_point_moved(screen, monkeypatch):
    screen.els = [_el("Button", "Send", 100, 200)]
    _j(v2.observe())
    walks = _count_walks(monkeypatch, screen)
    monkeypatch.setattr(v2.ax, "labels_at", lambda x, y: ["Something else"])
    r = _j(v2.act("e1.1", "click"))
    assert r["ok"] and walks["n"] == 2  # slow path: pre-walk, then after-walk


def test_title_expect_polls_the_cheap_title_not_the_whole_tree(screen, monkeypatch):
    screen.els = [_el("Button", "Go", 1, 1)]
    walks = _count_walks(monkeypatch, screen)
    titles = iter(["Page", "Page", "Page", "Done page"])
    monkeypatch.setattr(v2.ax, "window_title", lambda app="": next(titles, "Done page"))
    r = _j(v2.act("Go", "click", expect={"title_has": "done"}))
    assert r["ok"] and r["expect"] == "met" and walks["n"] == 3  # pre-walk, first after-walk, one final read; polls in between are title-only


def test_a_failed_act_does_not_renumber_the_ids_the_agent_already_holds(screen):
    screen.els = [_el("TextField", "Name", 1, 1), _el("TextField", "Email", 2, 2), _el("Button", "Submit", 3, 3)]
    o = _j(v2.observe())
    ids = {e["label"]: e["id"] for e in o["elements"]}
    screen.els = [_el("Link", "Banner", 0, 0)] + screen.els  # something new renders first: every number shifts by one
    bad = _j(v2.act("Your name", "type", text="x"))  # misses: the engine walks the screen again to look for candidates
    assert bad["error"]["code"] == "not_found"
    r = _j(v2.act(ids["Email"], "type", text="ada@example.com"))  # the old id still means Email, not whatever now sits at that number
    assert r["ok"] and screen.clicks[-1] == (2, 2)


def test_a_bare_or_unknown_id_is_rejected_not_guessed(screen):
    screen.els = [_el("Button", "Send")]
    _j(v2.observe())
    assert _j(v2.act("e1", "click"))["error"]["code"] == "not_found"  # no revision: treated as a label, matches nothing
    assert _j(v2.act("e1.99", "click"))["error"]["code"] == "unknown_id"
    assert screen.clicks == []


def test_many_acts_do_not_age_out_the_ids_from_one_observe(screen, monkeypatch):
    screen.els = [_el("TextField", f"Field {i}", i, i) for i in range(8)]
    o = _j(v2.observe())
    ids = [e["id"] for e in o["elements"]]
    monkeypatch.setattr(v2.ax, "labels_at", lambda x, y: [])  # force the slow path every time: pre-walk and after-walk
    for i in ids:
        assert _j(v2.act(i, "click"))["ok"]  # eight acts in a row on ids from the same observe
