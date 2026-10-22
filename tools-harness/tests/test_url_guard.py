import pytest

from tools.url_guard import navigable


@pytest.mark.parametrize("u", ["https://a.com", "http://localhost:9234/x", "about:blank"])
def test_allowed(u):
    assert navigable(u)


@pytest.mark.parametrize(
    "u", ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "chrome://settings", "amazon.com", ""]
)
def test_blocked(u):
    assert not navigable(u)


def test_browser_navigate_refuses_before_launching(monkeypatch):
    from tools import browser

    monkeypatch.setattr(browser, "_get_page", lambda: (_ for _ in ()).throw(AssertionError("launched")))
    assert browser.browser_navigate("file:///etc/passwd").startswith("Refused")


def test_headed_goto_refuses():
    from tools import headed_browser

    with pytest.raises(ValueError, match="Refused"):
        headed_browser._goto_inner("javascript:alert(1)")
