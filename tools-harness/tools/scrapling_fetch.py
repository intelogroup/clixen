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
import os
import re
import time
from pathlib import Path
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
                "logged_in": {
                    "type": "boolean",
                    "description": (
                        "Attach over CDP to the user's own logged-in Chrome (the browser "
                        "agent-browser drives) so the page renders with their session. "
                        "Use for account pages; default false."
                    ),
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


_LOGGED_IN_CDP = "http://localhost:9222"  # Chrome started with --remote-debugging-port=9222


def _scrapling_stealthy_fetch(
    url: str,
    timeout: int = 60,
    solve_cloudflare: bool = False,
    proxy: str | None = None,
    logged_in: bool = False,
) -> str:
    """Stealthy fetch with real Chromium. Returns JSON with html + status.
    logged_in=True attaches over CDP to the user's own logged-in Chrome (the one agent-browser
    drives) instead of launching a fresh profile; fixed to localhost so no arbitrary CDP target."""
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
    if logged_in:
        kwargs.update(cdp_url=_LOGGED_IN_CDP, headless=False)

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


def _adaptive_storage():
    """Point scrapling's fingerprint DB at a writable, per-user place.

    Its default is ``scrapling/elements_storage.db`` — *inside the installed
    package*, which is read-only in a signed ``.app`` and replaced on upgrade, so
    every saved selector would be lost. The library reads its module-level
    ``__DEFAULT_DB_FILE__`` at call time, so we set that rather than pass a storage
    instance: it insists on an ``lru_cache``-wrapped class and constructs it with the
    default path anyway.

    Order: CLIXEN_SCRAPLING_DIR, then the sidecar's data dir (the vault when the app
    runs it), then the app's Application Support folder.
    """
    try:
        import scrapling.parser as sp

        override = os.environ.get("CLIXEN_SCRAPLING_DIR")
        if override:
            base = Path(override).expanduser()
        else:
            data_dir = os.environ.get("CLIXEN_DATA_DIR")
            if data_dir:
                base = Path(data_dir).expanduser() / "scrapling"
            else:
                base = Path.home() / "Library" / "Application Support" / "Clixen" / "scrapling"
        base.mkdir(parents=True, exist_ok=True)
        sp.__DEFAULT_DB_FILE__ = str(base / "elements_storage.db")
        return str(base / "elements_storage.db")
    except Exception:
        return None


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

    _adaptive_storage()  # pins the fingerprint DB before the parser reads it
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
                "as_markdown": {
                    "type": "boolean",
                    "description": (
                        "Return the whole page as clean Markdown instead of extracting one "
                        "selector (then selector is ignored). Use when you want to read the "
                        "page's content rather than pull one field."
                    ),
                },
                "cdp_url": {
                    "type": "string",
                    "description": "Attach to an already-running Chrome with a debug port, e.g. http://127.0.0.1:9222.",
                },
                "profile": {
                    "type": "string",
                    "description": "Persist cookies for this site under a named profile, so a login survives between calls.",
                },
            },
            "required": ["url"],
        },
    },
}


SCRAPLING_API_CAPTURE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "scrapling_api_capture",
        "description": (
            "Read the data a page loaded in the background: open the URL in a real "
            "browser, keep its XHR/fetch responses, and return the JSON bodies that "
            "match a URL pattern. Use when the numbers are rendered by JavaScript and "
            "scraping the visible text gives you layout instead of data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Page to open."},
                "url_pattern": {
                    "type": "string",
                    "description": (
                        "Regex matched against request URLs, e.g. 'api/products'. "
                        "Omit for the usual data shapes (api/graphql/json/search)."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max responses to return (default 8, max 20).",
                },
                "cdp_url": {"type": "string", "description": "Attach to a running Chrome debug port instead of launching one."},
                "profile": {"type": "string", "description": "Named persistent profile, for pages behind a login." },
            },
            "required": ["url"],
        },
    },
}


SCRAPLING_CRAWL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "scrapling_crawl",
        "description": (
            "Walk a site's own links from a start page and collect one selector from each "
            "page (titles, links, prices). Hard-capped at 5 pages / 40 items / 40 seconds, "
            "same domain only. Use for 'grab these from my docs site' jobs, not for "
            "scraping the open web."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_url": {"type": "string"},
                "item_selector": {"type": "string", "description": "CSS/XPath to collect per page, e.g. 'h2::text'."},
                "max_pages": {"type": "integer", "description": "1-10, default 5."},
                "extract": {"type": "string", "enum": ["text", "html", "attrs"]},
            },
            "required": ["start_url", "item_selector"],
        },
    },
}


def _profile_dir(name: str) -> str:
    """A persistent browser profile for one site, so a login survives.

    Cookies and localStorage are credentials. They go beside the other user data
    (the vault when the app runs the sidecar), never in the repo, and the directory is
    created 0700 so another local account cannot read them.

    Default is OFF: `profile=` must be passed explicitly, because silently persisting
    sessions would turn "look at this page" into "stay logged in as the user".
    """
    import os as _os

    data_dir = _os.environ.get("CLIXEN_DATA_DIR")
    base = (Path(data_dir).expanduser() if data_dir
            else Path.home() / "Library" / "Application Support" / "Clixen")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:64] or "default"
    target = base / "scrapling" / "profiles" / safe
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        target.chmod(0o700)
    except OSError:
        pass
    return str(target)


def _browser_kwargs(stealth: bool, cdp_url: str = "", profile: str = "") -> dict:
    """Options for the browser fetchers, shared by every scrapling tool here.

    `cdp_url` attaches to a browser that is ALREADY running with a debugging port.
    That is a dev/diagnostic path: ADR 0006 keeps a debug port out of the shipped
    app, so this only works when the user started such a browser themselves.
    """
    kwargs: dict = {"headless": True, "network_idle": True, "timeout": 45_000}
    if cdp_url:
        kwargs["cdp_url"] = cdp_url
        kwargs.pop("headless", None)  # the attached browser owns its own visibility
    elif profile:
        kwargs["user_data_dir"] = _profile_dir(profile)
    return kwargs


def _scrapling_crawl(
    start_url: str,
    item_selector: str,
    max_pages: int = 5,
    same_domain: bool = True,
    extract: str = "text",
) -> str:
    """Bounded same-site crawl: follow links, collect one selector per page.

    Deliberately small — 5 pages / 40 items / 40 s by default. The library's spider
    framework can run for hours with pause/resume, which is the wrong shape for an
    orb that answers in seconds; this is the useful 5% of it behind a hard cap, so a
    "grab the titles from my docs site" ask cannot become an accidental crawl of the
    internet.
    """
    import time as _time

    max_pages = max(1, min(int(max_pages or 5), 10))
    deadline = _time.time() + 40
    seen: set[str] = set()
    queue = [start_url]
    base_host = ""
    m = re.match(r"https?://([^/]+)", start_url)
    if m:
        base_host = m.group(1).lower()

    from urllib.parse import urljoin, urlparse

    pages_read = 0
    items: list[dict] = []
    errors: list[str] = []

    while queue and pages_read < max_pages and _time.time() < deadline and len(items) < 40:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        fetched = _scrapling_fetch(url)
        try:
            data = json.loads(fetched)
        except json.JSONDecodeError:
            errors.append(f"{url}: unfetchable")
            continue
        if not data.get("ok"):
            errors.append(f"{url}: {data.get('error', 'fetch failed')}")
            continue
        pages_read += 1
        html = data.get("html", "")

        if item_selector:
            found = _scrapling_extract(
                html, item_selector, domain=base_host, extract=extract, mode="raw"
            )
            try:
                payload = json.loads(found)
                for value in payload.get("results", []):
                    items.append({"page": url, "value": value})
            except json.JSONDecodeError:
                pass

        if same_domain and pages_read < max_pages:
            for href in re.findall(r'href=["\']([^"\'#]+)', html):
                nxt = urljoin(url, href)
                host = urlparse(nxt).netloc.lower()
                if host != base_host or nxt in seen:
                    continue
                if urlparse(nxt).path.endswith((".pdf", ".zip", ".png", ".jpg", ".mp4")):
                    continue
                if len(queue) + pages_read < max_pages * 3:
                    queue.append(nxt)

    return json.dumps({
        "ok": bool(items or pages_read),
        "start": start_url,
        "pages_read": pages_read,
        "items_found": len(items),
        "truncated": len(items) >= 40 or pages_read >= max_pages,
        "items": items[:40],
        "errors": errors[:5],
    })


def _scrapling_markdown(url: str, stealth: bool = False, main_content_only: bool = True,
                        cdp_url: str = "", profile: str = "") -> str:
    """A page as clean, LLM-ready Markdown — no selector, no boilerplate.

    `read_page_text` returns the extension's innerText; this returns structure:
    headings, lists, links and tables preserved, navigation and scripts dropped.
    Measured 434 ms and 3 001 chars on quotes.toscrape.com, where the raw HTML is
    ~7 KB of markup for the same content. Needs scrapling[rag] (markdownify).
    """
    try:
        from scrapling.fetchers import Fetcher, StealthyFetcher
    except Exception as exc:  # pragma: no cover - dependency problem
        return _err(f"Scrapling unavailable: {exc}")

    try:
        if stealth or cdp_url or profile:
            page = StealthyFetcher.fetch(url, **_browser_kwargs(stealth, cdp_url, profile))
        else:
            page = Fetcher.get(url, timeout=30)
    except Exception as exc:
        return _err(f"Fetch failed: {type(exc).__name__}: {exc}")

    try:
        markdown = page.markdown(main_content_only=main_content_only)
    except ModuleNotFoundError as exc:
        return _err(f"Markdown needs scrapling[rag]: {exc}")
    except Exception as exc:
        return _err(f"Markdown failed: {type(exc).__name__}: {exc}")

    if not markdown or not markdown.strip():
        return _err("No readable content on that page")
    return json.dumps({
        "ok": True,
        "url": url,
        "chars": len(markdown),
        "markdown": markdown,
    })


def _scrapling_api_capture(url: str, url_pattern: str = "", limit: int = 8,
                           cdp_url: str = "", profile: str = "") -> str:
    """The JSON the page itself loaded — read without re-requesting it.

    Most modern pages render from XHR/fetch responses, so the numbers you can see
    on screen are in a JSON body, not in the HTML. With record_requests enabled the
    browser keeps those responses, and this returns the text bodies matching a URL
    pattern. Cheaper and far more reliable than scraping the rendered text of, say,
    a price table or a search result list.

    Real browser, so it costs seconds (measured 6.7 s cold on example.com) — prefer
    scrapling_fetch_and_extract for static pages.
    """
    try:
        from scrapling.fetchers import StealthySession
    except Exception as exc:  # pragma: no cover
        return _err(f"Scrapling unavailable: {exc}")

    # Default: the shapes that carry data rather than assets.
    pattern = url_pattern or r"(?i)/(api|graphql|json|data|search|list)\b|\.json(\?|$)"
    try:
        options = _browser_kwargs(True, cdp_url, profile)
        with StealthySession(
            **options,
            record_requests=True,
            max_recorded_requests=120,
            block_ads=True,
        ) as session:
            session.fetch(url)
            records = session.network.search(url_pattern=pattern, limit=max(1, min(limit, 20)))
            payload = []
            for record in records:
                body = ""
                try:
                    raw = record.body
                    body = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                except Exception:
                    body = ""
                payload.append({
                    "url": getattr(record, "url", ""),
                    "status": getattr(record, "status", None),
                    "bytes": len(body),
                    "body": body[:1500],
                })
    except Exception as exc:
        return _err(f"Capture failed: {type(exc).__name__}: {exc}")

    if not payload:
        return json.dumps({
            "ok": False,
            "url": url,
            "pattern": pattern,
            "error": "no matching network responses",
            "hint": "the page may server-render its data; use scrapling_fetch_and_extract",
        })
    return json.dumps({
        "ok": True,
        "url": url,
        "pattern": pattern,
        "count": len(payload),
        "responses": payload,
    })


def _scrapling_fetch_and_extract(
    url: str,
    selector: str,
    extract: str = "text",
    stealth: bool = False,
    as_markdown: bool = False,
) -> str:
    """Fetch + extract in one call. Returns JSON with results + diagnostics."""
    if as_markdown:
        return _scrapling_markdown(url, stealth=stealth)

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
