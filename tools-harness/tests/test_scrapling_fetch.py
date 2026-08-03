"""
Tests for tools/scrapling_fetch.py — Scrapling HTTP fetcher + adaptive parser.

Covers:
- Schema registration in tools/registry.py (4 new tools)
- _scrapling_fetch: returns HTML + diagnostics
- _scrapling_stealthy_fetch: same shape (we mock since StealthyFetcher is heavy)
- _scrapling_extract: 3 modes (auto_save / adaptive / raw) plus the 4 extract types
- _scrapling_fetch_and_extract: combines fetch + extract
- URL validation
- Error paths (empty HTML, missing scrapling, invalid URL)
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _clean_scrapling_db():
    """Remove scrapling adaptive storage so tests don't pollute each other."""
    try:
        import scrapling
        db = os.path.join(os.path.dirname(scrapling.__file__), "elements_storage.db")
        if os.path.exists(db):
            os.remove(db)
    except ImportError:
        pass


def _fake_response(html="<html><body><h1 id=title>Hello</h1><p>World</p></body></html>", status=200):
    """Mock scrapling Response (a Selector subclass with html_content, text, status)."""
    el = MagicMock()
    el.text = "Hello"
    el.html_content = '<h1 id="title">Hello</h1>'
    el.attrib = {"id": "title"}

    page = MagicMock()
    page.html_content = html
    page.text = "Hello World"
    page.status = status
    return page, [el]


class TestSchemaRegistration(unittest.TestCase):
    """Verify all 4 scrapling tools are exposed via the registry."""

    def setUp(self):
        _clean_scrapling_db()
        from tools.registry import ALL_TOOLS, EXECUTORS
        self.all_tools = ALL_TOOLS
        self.executors = EXECUTORS

    def _has_tool(self, name):
        for schema in self.all_tools:
            if schema.get("function", {}).get("name") == name:
                return True
        return False

    def test_scrapling_fetch_registered(self):
        assert self._has_tool("scrapling_fetch"), "scrapling_fetch missing from ALL_TOOLS"
        assert "scrapling_fetch" in self.executors

    def test_scrapling_stealthy_fetch_registered(self):
        assert self._has_tool("scrapling_stealthy_fetch")
        assert "scrapling_stealthy_fetch" in self.executors

    def test_scrapling_extract_registered(self):
        assert self._has_tool("scrapling_extract")
        assert "scrapling_extract" in self.executors

    def test_scrapling_fetch_and_extract_registered(self):
        assert self._has_tool("scrapling_fetch_and_extract")
        assert "scrapling_fetch_and_extract" in self.executors

    def test_schemas_have_required_fields(self):
        from tools.scrapling_fetch import SCHEMA, STEALTHY_SCHEMA, EXTRACT_SCHEMA, FETCH_AND_EXTRACT_SCHEMA

        for schema in (SCHEMA, STEALTHY_SCHEMA, EXTRACT_SCHEMA, FETCH_AND_EXTRACT_SCHEMA):
            params = schema["function"]["parameters"]
            assert "url" in params["properties"] or "html" in params["properties"]
            assert isinstance(params.get("required"), list) and len(params["required"]) > 0


class TestScraplingFetch(unittest.TestCase):
    """_scrapling_fetch: HTTP fetcher."""

    def setUp(self):
        _clean_scrapling_db()
        from tools.scrapling_fetch import _scrapling_fetch
        self.execute = _scrapling_fetch

    def test_url_must_start_with_http(self):
        result = json.loads(self.execute("not-a-url"))
        assert result["ok"] is False
        assert "http" in result["error"].lower()

    def test_returns_html_when_ok(self):
        page, _ = _fake_response()
        with patch("tools.scrapling_fetch.Fetcher") as MockFetcher:
            MockFetcher.get.return_value = page
            result = json.loads(self.execute("https://example.com"))
        assert result["ok"] is True
        assert result["html"] == page.html_content
        assert result["html_length"] == len(page.html_content)
        assert result["status"] == 200
        assert "time_ms" in result

    def test_fetch_error_returns_error_json(self):
        with patch("tools.scrapling_fetch.Fetcher") as MockFetcher:
            MockFetcher.get.side_effect = ConnectionError("refused")
            result = json.loads(self.execute("https://example.com"))
        assert result["ok"] is False
        assert "ConnectionError" in result["error"]


class TestScraplingStealthyFetch(unittest.TestCase):
    """_scrapling_stealthy_fetch: real Chromium stealth fetcher."""

    def setUp(self):
        _clean_scrapling_db()
        from tools.scrapling_fetch import _scrapling_stealthy_fetch
        self.execute = _scrapling_stealthy_fetch

    def test_url_must_start_with_http(self):
        result = json.loads(self.execute("bad-url"))
        assert result["ok"] is False
        assert "http" in result["error"].lower()

    def test_stealthy_success(self):
        page, _ = _fake_response()
        # StealthyFetcher is imported lazily inside the function — patch the source module
        with patch("scrapling.StealthyFetcher") as MockSF:
            MockSF.fetch.return_value = page
            result = json.loads(self.execute("https://bot-protected.com", solve_cloudflare=True))
        assert result["ok"] is True
        assert result["html"] == page.html_content
        # solve_cloudflare flag should have been passed
        MockSF.fetch.assert_called_once()
        kwargs = MockSF.fetch.call_args.kwargs
        assert kwargs.get("solve_cloudflare") is True


class TestScraplingExtract(unittest.TestCase):
    """_scrapling_extract: adaptive parser with 3 modes + 4 extract types."""

    def setUp(self):
        _clean_scrapling_db()
        from tools.scrapling_fetch import _scrapling_extract
        self.execute = _scrapling_extract

    def test_empty_html(self):
        result = json.loads(self.execute("", "h1"))
        assert result["ok"] is False
        assert "empty" in result["error"].lower()

    def test_empty_selector(self):
        result = json.loads(self.execute("<html></html>", ""))
        assert result["ok"] is False
        assert "empty" in result["error"].lower()

    def test_auto_save_mode(self):
        page, elements = _fake_response()
        with patch("tools.scrapling_fetch.Selector") as MockSel:
            MockSel.return_value = page
            page.css.return_value = elements
            result = json.loads(self.execute(
                html='<html><body><h1 id="title">Hello</h1></body></html>',
                selector="#title",
                mode="auto_save",
                extract="text",
            ))
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["results"] == ["Hello"]
        # auto_save=True should have been passed
        page.css.assert_called_once()
        assert page.css.call_args.kwargs.get("auto_save") is True

    def test_adaptive_mode(self):
        page, elements = _fake_response()
        with patch("tools.scrapling_fetch.Selector") as MockSel:
            MockSel.return_value = page
            page.css.return_value = elements
            result = json.loads(self.execute(
                html="<html><body></body></html>",
                selector=".old-class",  # selector doesn't exist anymore
                mode="adaptive",
                extract="text",
            ))
        assert result["ok"] is True
        assert result["count"] == 1
        # adaptive=True should have been passed (not auto_save)
        page.css.assert_called_once()
        call_kwargs = page.css.call_args.kwargs
        assert call_kwargs.get("adaptive") is True
        assert "auto_save" not in call_kwargs

    def test_raw_mode(self):
        page, elements = _fake_response()
        with patch("tools.scrapling_fetch.Selector") as MockSel:
            MockSel.return_value = page
            page.css.return_value = elements
            result = json.loads(self.execute(
                html="<html><body><h1>X</h1></body></html>",
                selector="h1",
                mode="raw",
                extract="text",
            ))
        assert result["ok"] is True
        call_kwargs = page.css.call_args.kwargs
        # raw mode: no auto_save, no adaptive
        assert not call_kwargs.get("auto_save", False)
        assert not call_kwargs.get("adaptive", False)

    def test_extract_text(self):
        page, elements = _fake_response()
        with patch("tools.scrapling_fetch.Selector") as MockSel:
            MockSel.return_value = page
            page.css.return_value = elements
            result = json.loads(self.execute("<h1>X</h1>", "h1", extract="text"))
        assert result["results"] == ["Hello"]

    def test_extract_html(self):
        page, elements = _fake_response()
        with patch("tools.scrapling_fetch.Selector") as MockSel:
            MockSel.return_value = page
            page.css.return_value = elements
            result = json.loads(self.execute("<h1>X</h1>", "h1", extract="html"))
        assert result["results"] == ['<h1 id="title">Hello</h1>']

    def test_extract_attrs(self):
        page, elements = _fake_response()
        with patch("tools.scrapling_fetch.Selector") as MockSel:
            MockSel.return_value = page
            page.css.return_value = elements
            result = json.loads(self.execute("<h1>X</h1>", "h1", extract="attrs"))
        assert result["results"] == [{"id": "title"}]

    def test_extract_json(self):
        page, elements = _fake_response()
        with patch("tools.scrapling_fetch.Selector") as MockSel:
            MockSel.return_value = page
            page.css.return_value = elements
            result = json.loads(self.execute("<h1>X</h1>", "h1", extract="json"))
        assert len(result["results"]) == 1
        item = result["results"][0]
        assert item["text"] == "Hello"
        assert item["html"] == '<h1 id="title">Hello</h1>'
        assert item["attrs"] == {"id": "title"}

    def test_xpath_selector_routes_to_xpath(self):
        page, elements = _fake_response()
        with patch("tools.scrapling_fetch.Selector") as MockSel:
            MockSel.return_value = page
            page.xpath.return_value = elements
            result = json.loads(self.execute("<h1>X</h1>", "//h1", extract="text"))
        assert result["ok"] is True
        page.xpath.assert_called_once()
        page.css.assert_not_called()

    def test_domain_sets_storage_key(self):
        page, _ = _fake_response()
        with patch("tools.scrapling_fetch.Selector") as MockSel:
            MockSel.return_value = page
            page.css.return_value = []
            self.execute("<h1>X</h1>", "h1", domain="news.example.com")
        # url= should be set on Selector for storage keying
        call_kwargs = MockSel.call_args.kwargs
        assert call_kwargs.get("url") == "https://news.example.com/"


class TestScraplingFetchAndExtract(unittest.TestCase):
    """_scrapling_fetch_and_extract: pipeline helper."""

    def setUp(self):
        _clean_scrapling_db()
        from tools.scrapling_fetch import _scrapling_fetch_and_extract
        self.execute = _scrapling_fetch_and_extract

    def test_combines_fetch_and_extract(self):
        page, elements = _fake_response()
        with patch("tools.scrapling_fetch.Fetcher") as MockFetcher, \
             patch("tools.scrapling_fetch.Selector") as MockSel:
            MockFetcher.get.return_value = page
            MockSel.return_value = page
            page.css.return_value = elements

            result = json.loads(self.execute(
                url="https://example.com",
                selector="h1",
                extract="text",
            ))
        assert result["ok"] is True
        assert result["url"] == "https://example.com"
        assert result["domain"] == "example.com"
        assert result["count"] == 1
        assert result["results"] == ["Hello"]
        assert "fetch_time_ms" in result
        assert "html_length" in result

    def test_stealth_flag_uses_stealthy_fetcher(self):
        page, elements = _fake_response()
        with patch("scrapling.StealthyFetcher") as MockSF, \
             patch("tools.scrapling_fetch.Selector") as MockSel:
            MockSF.fetch.return_value = page
            MockSel.return_value = page
            page.css.return_value = []

            result = json.loads(self.execute(
                url="https://bot-protected.com",
                selector="h1",
                stealth=True,
            ))
        assert result["ok"] is True
        MockSF.fetch.assert_called_once()

    def test_propagates_fetch_error(self):
        with patch("tools.scrapling_fetch.Fetcher") as MockFetcher:
            MockFetcher.get.side_effect = ConnectionError("net err")
            result = json.loads(self.execute(
                url="https://example.com",
                selector="h1",
            ))
        assert result["ok"] is False
        assert "ConnectionError" in result["error"]


class TestErrorPaths(unittest.TestCase):
    """Edge cases that should produce clean errors, not crash."""

    def setUp(self):
        _clean_scrapling_db()
        from tools.scrapling_fetch import _scrapling_fetch, _scrapling_stealthy_fetch, _scrapling_extract
        self.fetch = _scrapling_fetch
        self.stealthy = _scrapling_stealthy_fetch
        self.extract = _scrapling_extract

    def test_missing_scrapling_returns_error(self):
        with patch.dict(sys.modules, {"scrapling": None}):
            from tools import scrapling_fetch
            with patch.object(scrapling_fetch, "_HAS_SCRAPLING", False):
                result = json.loads(scrapling_fetch._scrapling_fetch("https://example.com"))
                assert result["ok"] is False
                assert "Scrapling not installed" in result["error"]

    def test_all_executors_handle_missing_args_gracefully(self):
        """Required-param errors should bubble up cleanly (KeyError) for the LLM to learn from."""
        from tools.registry import EXECUTORS

        # scrapling_extract: missing html → KeyError on the lambda (LLM learns: required)
        try:
            EXECUTORS["scrapling_extract"]({"selector": "h1"})
            raised = None
        except KeyError as e:
            raised = e
        assert raised is not None and "html" in str(raised), (
            "scrapling_extract should raise KeyError on missing 'html' param"
        )


if __name__ == "__main__":
    unittest.main()
