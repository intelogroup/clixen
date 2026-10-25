"""Scrapling in the orb: the self-healing selector, and where its memory lives.

The point of granting it is narrow: sites redesign, and a hard-coded selector then
returns nothing (or worse, the wrong element). Scrapling saves a structural
fingerprint on the first read and RELOCATES the element afterwards. These tests hold
that behaviour, plus the storage rule — fingerprints must not land in the cwd, which
is read-only inside a signed .app.
"""
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS))

from tools import scrapling_fetch as sf  # noqa: E402

PAGE_V1 = """<html><body><div class="card"><h2 class="product-title">Widget</h2>
<span class="price">$19.99</span></div></body></html>"""

# The site renamed .price -> .cost and moved it a level deeper. A CSS selector from
# yesterday returns nothing here; the adaptive lookup should still find $19.99.
PAGE_V2 = """<html><body><div class="card"><h2 class="product-title">Widget</h2>
<div class="buybox"><span class="cost">$19.99</span></div></div></body></html>"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIXEN_SCRAPLING_DIR", str(tmp_path / "scrapling"))
    return tmp_path


def test_fingerprint_storage_follows_the_data_dir_not_the_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIXEN_SCRAPLING_DIR", raising=False)
    monkeypatch.setenv("CLIXEN_DATA_DIR", str(tmp_path / "vault" / "data"))
    sf._adaptive_storage()
    assert (tmp_path / "vault" / "data" / "scrapling").is_dir()


def test_explicit_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIXEN_SCRAPLING_DIR", str(tmp_path / "custom"))
    sf._adaptive_storage()
    assert (tmp_path / "custom").is_dir()


def test_a_saved_selector_relocates_after_the_markup_changes(store):
    first = sf._scrapling_extract(PAGE_V1, ".price", domain="shop.test", mode="auto_save")
    assert "19.99" in first, first

    # Straight CSS on the new page returns nothing — that is the failure being fixed.
    raw = sf._scrapling_extract(PAGE_V2, ".price", domain="shop.test", mode="raw")
    assert "19.99" not in raw, f"the selector was expected to break: {raw}"

    healed = sf._scrapling_extract(PAGE_V2, ".price", domain="shop.test", mode="adaptive", similarity=40)
    assert "19.99" in healed, f"adaptive relocation did not find the element: {healed}"


def test_bad_selectors_return_a_message_instead_of_raising(store):
    out = sf._scrapling_extract(PAGE_V1, "[[[not a selector", domain="shop.test")
    assert isinstance(out, str) and "ok" in out.lower()


def test_empty_inputs_are_refused(store):
    assert "Empty HTML" in sf._scrapling_extract("", ".price", domain="x.test")
    assert "Empty selector" in sf._scrapling_extract(PAGE_V1, "", domain="x.test")


# ── markdown + background-API capture ────────────────────────────────────────
# Both are exercised with fakes here: the real ones need a browser, which CI has no
# business downloading. The measured live numbers are in the commit message.


def test_markdown_needs_no_selector(monkeypatch):
    class Page:
        status = 200

        @staticmethod
        def markdown(main_content_only=True):
            return "# Price board\n\n- Kettle: $39.50\n- Grinder: $112.00\n"

    class Fetcher:
        @staticmethod
        def get(url, timeout=30):
            return Page()

    import sys as _sys
    fake = type(_sys)("scrapling.fetchers")
    fake.Fetcher = Fetcher
    fake.StealthyFetcher = Fetcher
    monkeypatch.setitem(_sys.modules, "scrapling.fetchers", fake)

    out = sf._scrapling_fetch_and_extract("https://shop.test/", "", as_markdown=True)
    payload = __import__("json").loads(out)
    assert payload["ok"] and "Kettle" in payload["markdown"]
    assert payload["chars"] > 0


def test_api_capture_returns_the_bodies_and_says_which_url_matched(monkeypatch):
    class Record:
        url = "https://shop.test/api/products"
        status = 200
        body = b'{"items":[{"name":"Kettle","price":39.5}]}'

    class Network:
        @staticmethod
        def search(url_pattern=None, limit=100):
            assert "api" in (url_pattern or "")
            return [Record()]

    class Session:
        def __init__(self, **kw):
            assert kw.get("record_requests") is True, "capture needs recording enabled"
            self.network = Network()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def fetch(self, url):
            return None

    import sys as _sys
    fake = type(_sys)("scrapling.fetchers")
    fake.StealthySession = Session
    monkeypatch.setitem(_sys.modules, "scrapling.fetchers", fake)

    out = sf._scrapling_api_capture("https://shop.test/", url_pattern="api", limit=5)
    payload = __import__("json").loads(out)
    assert payload["ok"] is True and payload["count"] == 1
    assert "Kettle" in payload["responses"][0]["body"]
    assert payload["responses"][0]["url"].endswith("/api/products")


def test_api_capture_with_no_matches_says_so_and_suggests_the_alternative(monkeypatch):
    class Session:
        def __init__(self, **kw):
            class N:
                @staticmethod
                def search(url_pattern=None, limit=100):
                    return []
            self.network = N()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def fetch(self, url):
            return None

    import sys as _sys
    fake = type(_sys)("scrapling.fetchers")
    fake.StealthySession = Session
    monkeypatch.setitem(_sys.modules, "scrapling.fetchers", fake)

    payload = __import__("json").loads(sf._scrapling_api_capture("https://shop.test/"))
    assert payload["ok"] is False
    assert "scrapling_fetch_and_extract" in payload["hint"]
