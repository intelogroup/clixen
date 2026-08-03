import os
import sys
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.fetch_url import _extract_text, _normalise_url, execute


class _FakeResponse:
    def __init__(self, text, status_code=200, content_type="text/html"):
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def json(self):
        import json as _json

        return _json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)


class _FakeSession:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    def get(self, url, headers=None):
        if self._exc:
            raise self._exc
        return self._response


def test_normalise_url_adds_https_scheme():
    assert _normalise_url("example.com/path") == "https://example.com/path"


def test_extract_text_prefers_title_and_main_content():
    html = """
    <html>
      <head><title>Example Title</title></head>
      <body>
        <nav>skip me</nav>
        <main>
          <h1>This is the main headline with enough words</h1>
          <p>This paragraph contains enough content to survive filtering and be returned.</p>
        </main>
      </body>
    </html>
    """

    title, text = _extract_text(html)

    assert title == "Example Title"
    assert "main headline" in text.lower()
    assert "skip me" not in text.lower()


def test_execute_returns_page_title_and_trimmed_content():
    html = """
    <html>
      <head><title>Product Page</title></head>
      <body>
        <main>
          <p>This product page contains enough readable text to be extracted successfully for the model.</p>
          <p>Second paragraph with useful details about specs, pricing, and availability.</p>
        </main>
      </body>
    </html>
    """

    with patch("tools.fetch_url._get_session", return_value=_FakeSession(response=_FakeResponse(html))):
        result = execute("example.com/product", max_chars=120, _fallback=False)

    assert "URL: https://example.com/product" in result
    assert "Title: Product Page" in result
    assert "[...truncated]" in result


def test_execute_adds_spa_note_when_playwright_unavailable():
    html = """
    <html>
      <head><title>SPA App</title><script>window.__INITIAL_STATE__={}</script></head>
      <body><main><p>This page has enough static text to be returned despite SPA detection for testing.</p></main></body>
    </html>
    """

    with patch("tools.fetch_url._get_session", return_value=_FakeSession(response=_FakeResponse(html))), \
         patch("tools.fetch_url._playwright_fallback", return_value=None):
        result = execute("https://example.com/app", _fallback=False)

    assert "[fetch_note]" in result
    assert "SPA/thin content detected" in result


def test_execute_returns_browser_rendered_when_playwright_succeeds():
    spa_html = """
    <html>
      <head><title>SPA App</title><script>window.__NEXT_DATA__={}</script></head>
      <body></body>
    </html>
    """
    pw_content = "Rendered content from Playwright with real data and numbers: 42000 cases reported."

    with patch("tools.fetch_url._get_session", return_value=_FakeSession(response=_FakeResponse(spa_html))), \
         patch("tools.fetch_url._playwright_fallback", return_value=pw_content):
        result = execute("https://example.com/spa", _fallback=False)

    assert "[browser-rendered]" in result
    assert "42000 cases" in result
    assert "[fetch_note]" not in result


def test_execute_js_domain_skips_httpx():
    pw_content = "Live WHO dashboard data: 700 million total cases worldwide."

    with patch("tools.fetch_url._playwright_fallback", return_value=pw_content) as mock_pw:
        result = execute("https://data.who.int/dashboards/covid19/cases", _fallback=False)

    mock_pw.assert_called_once()
    call_kwargs = mock_pw.call_args
    assert call_kwargs.kwargs.get("timeout_s") == 8.0 or call_kwargs.args[2] == 8.0
    assert "[browser-rendered]" in result
    assert "700 million" in result


def test_execute_js_domain_falls_through_to_httpx_if_playwright_fails():
    static_html = """
    <html>
      <head><title>WHO Fallback</title></head>
      <body><main><p>This is static fallback content long enough to satisfy the extraction threshold.</p>
      <p>Second paragraph ensures minimum content length is met during test execution.</p></main></body>
    </html>
    """

    with patch("tools.fetch_url._playwright_fallback", return_value=None), \
         patch("tools.fetch_url._get_session", return_value=_FakeSession(response=_FakeResponse(static_html))):
        result = execute("https://data.who.int/dashboards/covid19/cases", _fallback=False)

    assert "WHO Fallback" in result


def test_playwright_fallback_trylock_skips_if_locked():
    import tools.fetch_url as fu

    # Simulate lock already held by another thread
    fu._playwright_in_flight.acquire()
    try:
        result = fu._playwright_fallback("https://example.com", max_chars=500)
    finally:
        fu._playwright_in_flight.release()

    assert result is None


def test_execute_uses_api_fallback_on_http_error():
    response = _FakeResponse("", status_code=500)

    with patch("tools.fetch_url._get_session", return_value=_FakeSession(response=response)), \
         patch("tools.fetch_url._api_fallback", return_value="fallback content") as mock_fallback:
        result = execute("https://example.com/fail")

    assert result == "fallback content"
    mock_fallback.assert_called_once()


def test_execute_supports_json_api_response():
    payload = '{"cases": 779207696, "deaths": 7114028, "updated": "2026-03-28"}'

    with patch(
        "tools.fetch_url._get_session",
        return_value=_FakeSession(response=_FakeResponse(payload, content_type="application/json; charset=utf-8")),
    ):
        result = execute("https://example.com/api/stats.json", _fallback=False)

    assert "Content-Type: application/json" in result
    assert '"cases": 779207696' in result
    assert "[fetch_failed]" not in result


def test_execute_supports_json_historical_api_response():
    payload = '{"cases": {"2026-03-01": 100, "2026-03-02": 200}, "deaths": {"2026-03-01": 3}}'

    with patch(
        "tools.fetch_url._get_session",
        return_value=_FakeSession(
            response=_FakeResponse(payload, content_type="application/json; charset=utf-8")
        ),
    ):
        result = execute("https://example.com/api/history.json", _fallback=False)

    assert "Content-Type: application/json" in result
    assert '"2026-03-01": 100' in result
    assert "[fetch_failed]" not in result
