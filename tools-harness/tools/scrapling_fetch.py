"""
Scrapling-based HTTP fetcher + adaptive parser.

Lightweight alternative to Playwright/Selenium for public pages that don't
require JS execution. Uses Scrapling's HTTP Fetcher (0.27-0.79s, TLS fingerprint
masquerade) for fetching, and the adaptive parser for self-healing extraction
when site DOM changes.

Optional dependency: if scrapling is not installed, this module logs a warning
once and all tools return a clear "not installed" error. Zero regression for
existing workflows.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

_log = logging.getLogger("scrapling_fetch")

try:
    from scrapling import Fetcher, Selector
    _HAS_SCRAPLING = True
except ImportError:
    _HAS_SCRAPLING = False
    _log.warning(
        "Scrapling not installed. scrapling_fetch tools will return errors. "
        "Install with: pip install 'scrapling[all]'"
    )


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})


def _datapoints_max_chrome_version() -> int | None:
    """Max Chrome major version the installed apify_fingerprint_datapoints
    header network has fingerprint data for (e.g. 141). None if unknown."""
    try:
        import json
        import os
        import zipfile

        from apify_fingerprint_datapoints import get_header_network  # noqa: F401  (import sanity)

        path = os.path.join(
            os.path.dirname(os.path.abspath(__import__("apify_fingerprint_datapoints").__file__)),
            "data",
            "header-network-definition.zip",
        )
        with zipfile.ZipFile(path) as z:
            net = json.loads(z.read("network.json"))
        majors: list[int] = []
        for node in net["nodes"]:
            if not node["name"].startswith("*BROWSER"):
                continue
            for value in node["possibleValues"]:
                m = re.match(r"^chrome/(\d+)(?:\.|$)", str(value))
                if m:
                    majors.append(int(m.group(1)))
        return max(majors) if majors else None
    except Exception:
        return None


def _clamp_scrapling_chrome_version() -> None:
    """scrapling hardcodes chromium_version/chrome_version (>=145) above what
    the bundled apify_fingerprint_datapoints header network supports (max 141),
    so `from scrapling.fetchers import StealthyFetcher` raises ValueError at
    import time. Clamp the constants to the max supported version so the real
    Chromium stealth fetcher actually works."""
    try:
        import scrapling.engines.toolbelt.fingerprints as _fp
    except Exception:
        return
    try:
        from scrapling.fetchers import StealthyFetcher  # noqa: F401

        return
    except ValueError:
        pass
    max_ver = _datapoints_max_chrome_version()
    if max_ver:
        for _attr in ("chromium_version", "chrome_version"):
            setattr(_fp, _attr, max_ver)


# ---------------------------------------------------------------------------
# Tool schema (Ollama function-calling format)
# ---------------------------------------------------------------------------

SCHEMA = {
    "type": "function",
    "function": {
        "name": "scrapling_fetch",
        "description": (
            "Fetch a public webpage with Scrapling's HTTP Fetcher (TLS fingerprint "
            "masquerade, 0.3-0.8s typical). Returns the rendered HTML. Faster than "
            "Playwright for public pages that don't require JS execution. For "
            "JS-heavy sites, use browser_navigate instead. For Cloudflare-protected "
            "pages, use scrapling_stealthy_fetch."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch (http/https)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Request timeout in seconds (default 30)",
                },
                "headless": {
                    "type": "boolean",
                    "description": "Run headless (default true)",
                },
                "proxy": {
                    "type": "string",
                    "description": "Optional proxy URL, e.g. http://user:pass@host:port",
                },
            },
            "required": ["url"],
        },
    },
}

STEALTHY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "scrapling_stealthy_fetch",
        "description": (
            "Fetch a webpage with Scrapling's StealthyFetcher (real Chromium, anti-bot "
            "evasion, ~2s). Slower than scrapling_fetch but bypasses most bot detection. "
            "Use for sites that block plain HTTP. Solves Cloudflare Turnstile interactive."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Request timeout in seconds (default 60)",
                },
                "solve_cloudflare": {
                    "type": "boolean",
                    "description": "Enable Cloudflare Turnstile solver (default false)",
                },
                "proxy": {
                    "type": "string",
                    "description": "Optional proxy URL",
                },
            },
            "required": ["url"],
        },
    },
}

EXTRACT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "scrapling_extract",
        "description": (
            "Parse HTML with Scrapling's adaptive parser. The first call extracts by "
            "the given CSS/XPath selector and SAVES the element fingerprint. Subsequent "
            "calls on similar pages (same domain) RELOCATE the element after site "
            "redesigns, surviving class/role/nesting/attribute changes above 40% "
            "structural similarity. Use for self-healing scrapers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "html": {
                    "type": "string",
                    "description": "HTML string to parse",
                },
                "selector": {
                    "type": "string",
                    "description": (
                        "CSS or XPath selector. First call: define the element. "
                        "Subsequent calls on the same domain: relocate the saved element."
                    ),
                },
                "domain": {
                    "type": "string",
                    "description": "Domain key for adaptive storage (e.g. 'm.uber.com'). "
                                   "Defaults to URL-derived key when fetching.",
                },
                "extract": {
                    "type": "string",
                    "enum": ["text", "html", "attrs", "json"],
                    "description": "What to return: text content, innerHTML, attributes dict, "
                                   "or full JSON representation (default 'text')",
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto_save", "adaptive", "raw"],
                    "description": (
                        "auto_save: extract + save fingerprint (default, use on first/known page). "
                        "adaptive: relocate saved element after site redesign (use on changed page). "
                        "raw: pure extraction, no fingerprint storage."
                    ),
                },
                "similarity": {
                    "type": "integer",
                    "description": "Minimum structural similarity % (0-100) for adaptive relocation "
                                   "(default 40, lower = more lenient)",
                },
            },
            "required": ["html", "selector"],
        },
    },
}


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

def _scrapling_fetch(
    url: str,
    timeout: int = 30,
    headless: bool = True,
    proxy: str | None = None,
) -> str:
    """Lightweight HTTP fetch. Returns JSON with html, status, url, time_ms."""
    if not _HAS_SCRAPLING:
        return _err("Scrapling not installed. pip install 'scrapling[all]'")

    if not url.startswith(("http://", "https://")):
        return _err(f"URL must start with http:// or https://, got: {url!r}")

    kwargs: dict[str, Any] = {
        "headless": headless,
        "timeout": timeout * 1000,  # scrapling uses ms
    }
    if proxy:
        kwargs["proxy"] = proxy

    t0 = time.time()
    try:
        kwargs.pop("headless", None)  # Fetcher (HTTP) has no headless arg
        page = Fetcher.get(url, **kwargs)
    except Exception as e:
        return _err(f"Fetch failed: {type(e).__name__}: {e}")

    elapsed_ms = int((time.time() - t0) * 1000)

    try:
        html = page.html_content if hasattr(page, "html_content") else str(page)
    except Exception as e:
        return _err(f"Could not read html: {e}")

    return json.dumps({
        "ok": True,
        "url": url,
        "status": getattr(page, "status", 200),
        "html": html,
        "html_length": len(html),
        "time_ms": elapsed_ms,
    })


def _scrapling_stealthy_fetch(
    url: str,
    timeout: int = 60,
    solve_cloudflare: bool = False,
    proxy: str | None = None,
) -> str:
    """Stealthy fetch with real Chromium. Returns JSON with html + status."""
    if not _HAS_SCRAPLING:
        return _err("Scrapling not installed. pip install 'scrapling[all]'")

    if not url.startswith(("http://", "https://")):
        return _err(f"URL must start with http:// or https://, got: {url!r}")

    kwargs: dict[str, Any] = {
        "headless": True,
        "timeout": timeout * 1000,
    }
    if proxy:
        kwargs["proxy"] = proxy
    if solve_cloudflare:
        kwargs["solve_cloudflare"] = True

    t0 = time.time()
    try:
        _clamp_scrapling_chrome_version()
        from scrapling import StealthyFetcher
        page = StealthyFetcher.fetch(url, **kwargs)
    except Exception as e:
        return _err(f"Stealthy fetch failed: {type(e).__name__}: {e}")

    elapsed_ms = int((time.time() - t0) * 1000)

    try:
        html = page.html_content if hasattr(page, "html_content") else str(page)
    except Exception as e:
        return _err(f"Could not read html: {e}")

    return json.dumps({
        "ok": True,
        "url": url,
        "status": getattr(page, "status", 200),
        "html": html,
        "html_length": len(html),
        "time_ms": elapsed_ms,
    })


def _scrapling_extract(
    html: str,
    selector: str,
    domain: str = "",
    extract: str = "text",
    mode: str = "auto_save",
    similarity: int = 40,
) -> str:
    """Parse HTML with adaptive parser. Returns JSON with matches."""
    if not _HAS_SCRAPLING:
        return _err("Scrapling not installed. pip install 'scrapling[all]'")

    if not html:
        return _err("Empty HTML")
    if not selector:
        return _err("Empty selector")

    kwargs: dict[str, Any] = {}
    if domain:
        kwargs["url"] = f"https://{domain}/"  # scrapling uses URL as storage key

    try:
        page = Selector(content=html, adaptive=True, **kwargs)
    except Exception as e:
        return _err(f"Parse failed: {type(e).__name__}: {e}")

    is_xpath = selector.startswith(("//", "/html", "(//"))

    try:
        if mode == "raw":
            # Pure extraction, no fingerprint
            if is_xpath:
                elements = page.xpath(selector)
            else:
                elements = page.css(selector)
        elif mode == "adaptive":
            # Relocate saved element (use after site redesign)
            if is_xpath:
                elements = page.xpath(selector, adaptive=True, percentage=similarity)
            else:
                elements = page.css(selector, adaptive=True, percentage=similarity)
        else:
            # Default: auto_save (extract + save fingerprint for first call)
            if is_xpath:
                elements = page.xpath(selector, auto_save=True)
            else:
                elements = page.css(selector, auto_save=True)
    except Exception as e:
        return _err(f"Selector failed: {type(e).__name__}: {e}")

    results = []
    for el in elements:
        if extract == "text":
            results.append(el.text)
        elif extract == "html":
            results.append(el.html_content)
        elif extract == "attrs":
            results.append(dict(el.attrib) if hasattr(el, "attrib") else {})
        elif extract == "json":
            results.append({
                "text": el.text,
                "html": el.html_content,
                "attrs": dict(el.attrib) if hasattr(el, "attrib") else {},
            })
        else:
            results.append(el.text)

    return json.dumps({
        "ok": True,
        "selector": selector,
        "domain": domain,
        "mode": mode,
        "count": len(results),
        "results": results,
    }, default=str)


# ---------------------------------------------------------------------------
# Pipeline helper: fetch + extract in one call
# ---------------------------------------------------------------------------

FETCH_AND_EXTRACT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "scrapling_fetch_and_extract",
        "description": (
            "One-call pipeline: fetch a URL with HTTP Fetcher and extract elements by "
            "CSS/XPath selector. Combines scrapling_fetch + scrapling_extract. Use for "
            "simple public-page scraping where you need both fetch and parse in one step."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS or XPath selector to extract",
                },
                "extract": {
                    "type": "string",
                    "enum": ["text", "html", "attrs", "json"],
                },
                "stealth": {
                    "type": "boolean",
                    "description": "Use StealthyFetcher (real Chromium) instead of HTTP Fetcher",
                },
            },
            "required": ["url", "selector"],
        },
    },
}


def _scrapling_fetch_and_extract(
    url: str,
    selector: str,
    extract: str = "text",
    stealth: bool = False,
) -> str:
    """Fetch + extract in one call. Returns JSON with results + diagnostics."""
    # Extract domain from URL for adaptive storage key
    domain = ""
    m = re.match(r"https?://([^/]+)", url)
    if m:
        domain = m.group(1)

    if stealth:
        fetch_result = _scrapling_stealthy_fetch(url)
    else:
        fetch_result = _scrapling_fetch(url)

    try:
        fetch_data = json.loads(fetch_result)
    except json.JSONDecodeError:
        return fetch_result

    if not fetch_data.get("ok"):
        return fetch_result

    html = fetch_data.get("html", "")
    if not html:
        return _err("Fetch returned empty HTML")

    extract_result = _scrapling_extract(html, selector, domain=domain, extract=extract)
    try:
        extract_data = json.loads(extract_result)
    except json.JSONDecodeError:
        return extract_result

    # Merge fetch diagnostics with extraction results
    return json.dumps({
        "ok": True,
        "url": url,
        "domain": domain,
        "fetch_time_ms": fetch_data.get("time_ms"),
        "html_length": fetch_data.get("html_length"),
        "status": fetch_data.get("status"),
        "selector": selector,
        "count": extract_data.get("count", 0),
        "results": extract_data.get("results", []),
    }, default=str)
