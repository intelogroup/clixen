import pytest

from tools import browse_guard, ax_context


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    browse_guard.reset()
    monkeypatch.delenv("CLIXEN_ALLOW_PURCHASE_CLICKS", raising=False)
    yield
    browse_guard.reset()


def test_purchase_click_is_refused_by_text_and_by_resolved_id(monkeypatch):
    msg = browse_guard.pre_check("click_text", {"text": "Add to cart, Hero Pup Academy"})
    assert msg and msg.startswith("[blocked]") and "NOT clicked" in msg
    from tools import screen_v2
    monkeypatch.setitem(screen_v2._state["hist"], 9, {"e3.9": {"fp": ("Button", "Buy now", 0)}, "e4.9": {"fp": ("Button", "Close", 0)}})
    assert browse_guard.pre_check("act", {"target": "e3.9", "action": "click"})
    assert browse_guard.pre_check("act", {"target": "e4.9", "action": "click"}) is None
    assert browse_guard.pre_check("click_text", {"text": "Done"}) is None


def test_purchase_clicks_can_be_allowed_explicitly(monkeypatch):
    monkeypatch.setenv("CLIXEN_ALLOW_PURCHASE_CLICKS", "1")
    assert browse_guard.pre_check("click_text", {"text": "Place your order"}) is None


def test_same_call_three_times_is_cut_off_but_observe_is_not():
    args = {"url": "https://www.amazon.com/s?k=omi"}
    assert browse_guard.pre_check("open_url", args) is None
    assert browse_guard.pre_check("open_url", args) is None
    assert "already called 2 times" in browse_guard.pre_check("open_url", args)
    assert browse_guard.pre_check("open_url", {"url": "https://www.walmart.com"}) is None  # different args
    for _ in range(6):
        assert browse_guard.pre_check("observe", {"app": "Google Chrome"}) is None


def test_read_tool_that_changes_the_page_gets_a_warning(monkeypatch):
    urls = iter(["https://a.example/search", "https://b.example/login"])
    monkeypatch.setattr(ax_context, "front_url", lambda app="": next(urls))
    before = browse_guard.url_before("read_page_text", {"app": "Google Chrome"})
    out = browse_guard.after("read_page_text", {"app": "Google Chrome"}, before, "page text")
    assert out.startswith("page text") and "[warning]" in out and "b.example/login" in out


def test_unchanged_page_and_unwatched_tools_are_left_alone(monkeypatch):
    monkeypatch.setattr(ax_context, "front_url", lambda app="": "https://a.example/")
    before = browse_guard.url_before("observe", {})
    assert browse_guard.after("observe", {}, before, "ok") == "ok"
    assert browse_guard.url_before("act", {}) is None
    assert browse_guard.after("act", {}, None, "ok") == "ok"
