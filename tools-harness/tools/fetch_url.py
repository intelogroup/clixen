"""
fetch_url — lightweight HTTP page fetcher.

Fetches a URL with httpx, strips HTML to readable text via BeautifulSoup,
and returns a trimmed result suitable for model context.

Use case: user asks "go to realme.com", "what's on this page", "check apple.com/iphone".
Not for browser automation (clicking, forms, login) — use browser_* tools for those.

Trimming budget: 3000 chars (~750 tokens). Enough for a homepage or product page summary.
"""

import re
import json
import threading
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
from bs4 import BeautifulSoup

_TIMEOUT = 10.0

# Known JS-heavy domains — skip httpx entirely, go straight to Playwright
_JS_DOMAINS = frozenset([
    "data.who.int",
    "dashboards.who.int",
    "data.cdc.gov",
    "ourworldindata.org",
    "gisanddata.maps.arcgis.com",
])

# Trylock: only one thread uses Playwright for enrichment at a time
# Others fall through to Tavily — prevents 3-thread pileup in enrich_node
_playwright_in_flight = threading.Lock()

# Rotating User-Agents to bypass basic anti-bot
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# SPA frameworks that render content client-side — httpx will only get the shell
_SPA_MARKERS = ["__NEXT_DATA__", "__NUXT__", "window.__INITIAL_STATE__", "ng-version", "data-reactroot"]

# Session for connection reuse
_session = None


def _get_session() -> httpx.Client:
    global _session
    if _session is None:
        _session = httpx.Client(timeout=_TIMEOUT, follow_redirects=False, verify=True)
    return _session


# Tags that are structurally noise — no readable content ever lives here.
# NOTE: <link> and <meta> are intentionally excluded: html.parser (and lxml)
# sometimes treat inline <link>/<meta> elements in <body> as containers,
# so decomposing them would also destroy surrounding <p> content (Wikipedia bug).
# Both are already inside <head> which is stripped here anyway.
_STRIP_TAGS = {
    "script",
    "style",
    "noscript",
    "header",
    "footer",
    "nav",
    "aside",
    "svg",
    "iframe",
    "form",
    "button",
    "input",
    "head",
}

# Universal heuristic: any element whose class or id contains one of these
# substrings is treated as chrome/boilerplate and removed before extraction.
# Covers navigation, cookies, ads, social bars, sidebars, donate prompts, etc.
# across any website — not just WHO/CDC.
_NOISE_ATTRS = re.compile(
    r"\b("
    # nav — also catches suffix compounds like topnav, mainnav, sitenav, subnav
    r"nav|navigation|navbar|navlist|navmenu|topnav|mainnav|sitenav|subnav|globalnav|"
    r"menu|menubar|megamenu|topmenu|sf-menu|"
    r"breadcrumb|crumb|"
    r"header|masthead|site-header|"
    r"footer|site-footer|"
    r"sidebar|side-bar|widget|"
    r"cookie|consent|gdpr|"
    r"banner|alert-bar|announcement|"
    r"promo|advertisement|advert|ads?[-_]|[-_]ads?|"
    r"social|share|sharing|"
    r"donate|donation|"
    r"newsletter|subscribe|subscription|"
    r"related|recommended|also-read|"
    r"tag-list|tags|"
    r"language|locale|lang-switcher|"
    r"search|searchbar|"
    r"print|email-page|syndicate|"
    r"skip-link|back-to-top|scroll-top|"
    r"pagination|pager|"
    r"toc|table-of-contents|"
    r"modal|overlay|popup|lightbox|"
    r"toolbar|utility-bar"
    r")\b"
    # Also catch nav/menu as a SUFFIX in compound words (topnav, sitemenu…)
    # by alternating with a no-leading-boundary pattern
    r"|(?:top|main|site|sub|global|primary|secondary|local|micro|jump|inner|outer|page)[-_]?(?:nav|menu|bar)(?:\b|[-_])",
    re.I,
)

_TABLE_NUMERIC_RE = re.compile(r"\d")

# Ordered list of content-container selectors tried before falling back to body.
# Each entry is (method, args, kwargs) for soup.find().
_CONTENT_SELECTORS = [
    # Semantic / ARIA
    ("find", [], {"attrs": {"role": "main"}}),
    ("find", ["main"], {}),
    ("find", ["article"], {}),
    # Common id patterns
    ("find", [], {"id": re.compile(r"^(main[-_]?content|content[-_]?main|primary|main|content)$", re.I)}),
    # Common class patterns — intentionally narrow to avoid sidebar matches
    ("find", [], {"class_": re.compile(r"\b(article[-_]body|post[-_]body|entry[-_]content|wysiwyg|sf-content-block|content-block)\b", re.I)}),
]


def _table_to_text(table) -> str:
    """Convert a BS4 <table> to pipe-delimited text; skips rows with no digits."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
        row = " | ".join(c for c in cells if c)
        if row and _TABLE_NUMERIC_RE.search(row):
            rows.append(row)
    return "\n".join(rows)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": (
            "Fetch and read the content of a web page. "
            "Use when the user asks to visit, open, or check a specific URL or website. "
            "Returns the page title and readable text content. "
            "Not for clicking or form interaction — use browser tools for that."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch (include https:// if not present).",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Max characters of page text to return. Default 3000.",
                    "default": 3000,
                },
            },
            "required": ["url"],
        },
    },
}


def _normalise_url(url: str) -> str:
    url = url.strip()
    # Only bare host/path (no "scheme://" at all) gets https:// prepended —
    # a url with any other scheme (file://, ftp://, ...) must pass through
    # unchanged so _check_ssrf sees the real scheme and blocks it, instead of
    # it being silently rewritten into "https://file://..." (bogus host,
    # fails safe by accident via DNS error, not by the scheme guard).
    if "://" not in url:
        url = "https://" + url
    return url


def _is_unsafe_ip(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified


def _check_ssrf(url: str) -> str | None:
    """Return an error string if the URL targets a private/loopback/cloud-metadata
    address, or uses a non-http(s) scheme.  Returns None if the URL is safe.

    Blocks:
    - Non-http(s) schemes (file://, ftp://, gopher://, …)
    - Loopback (127.x, ::1, localhost)
    - Link-local / cloud-metadata (169.254.x — AWS/GCP/Azure IMDS)
    - RFC-1918 private ranges (10.x, 172.16-31.x, 192.168.x)
    - Unroutable / special ranges (0.x, 100.64.x CGNAT, 198.18.x benchmarks)
    - DNS rebinding: a domain name (not a literal IP) whose A/AAAA records
      resolve to any of the above — a domain the attacker owns can point
      anywhere, including 127.0.0.1 or the cloud metadata IP (confirmed live:
      the public domain localtest.me resolves straight to 127.0.0.1). A
      reverse proxy in front of the target (e.g. Cloudflare) does not stop
      this — DNS resolution happens here, in this process, independent of
      any proxy the *destination* site uses.
    """
    import ipaddress
    import socket

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"[blocked] non-http scheme '{parsed.scheme}'"

    host = parsed.hostname or ""
    if not host:
        return "[blocked] missing host"

    # Reject by name before DNS resolution
    if host in ("localhost", "ip6-localhost", "ip6-loopback"):
        return f"[blocked] private host '{host}'"

    # Literal IP in the URL — check directly
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None:
        if _is_unsafe_ip(ip):
            return f"[blocked] private/reserved IP '{host}'"
        return None

    # Domain name — resolve and check every returned address (DNS rebinding guard).
    # A resolution *failure* here is not evidence of danger — it's the same
    # thing the httpx call below would hit — so let it fall through to the
    # normal fetch/fallback path (Tavily) instead of hard-blocking.
    try:
        addrs = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None

    for family, _, _, _, sockaddr in addrs:
        try:
            resolved_ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if _is_unsafe_ip(resolved_ip):
            return f"[blocked] host '{host}' resolves to private/reserved IP '{sockaddr[0]}'"

    # ponytail: checked here, connected via httpx afterward — a TOCTOU window
    # exists if DNS changes between this check and the actual request. Full
    # fix is pinning the resolved IP into the httpx connection; add if this
    # tool is ever exposed to untrusted/external callers, not just the LLM.
    return None


def _extract_text(html: str, url: str = "") -> tuple[str, str]:
    """Universal smart extractor — works for any page, not just specific domains.

    Pipeline:
    1. Strip structural noise tags (nav, header, footer, script, …)
    2. Strip elements whose class/id matches _NOISE_ATTRS (cookie banners, ads,
       social bars, sidebars, donate prompts, breadcrumbs, etc.)
    3. Extract <table> elements as pipe-delimited text (catches stat/data tables
       on health, finance, sports, or any data-heavy page) before prose pass.
    4. Find the best content container via _CONTENT_SELECTORS, fall back to body.
    5. Walk headings + paragraphs + list items; deduplicate.
    6. If yield is thin (<300 chars), try trafilatura as a last resort.
    """
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # 1. Strip structural noise tags
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    # 2. Strip elements by noisy class/id heuristics.
    # Guards:
    # (a) Skip root structural tags — their class names are framework/skin
    #     identifiers (e.g. Wikipedia's <html class="vector-feature-language-...">
    #     matches \blanguage\b) and must never be decomposed.
    # (b) BS4 4.12 decompose() clears __dict__ of the tag AND all descendants
    #     in the next_element chain. Siblings/children still in the find_all
    #     list will have attrs=None → catch AttributeError and skip them.
    _SKIP_ROOTS = frozenset({"html", "body", "main", "article", "section"})
    for tag in soup.find_all(True):
        if tag.name in _SKIP_ROOTS:
            continue
        try:
            cls = " ".join(tag.get("class", []))
            tid = tag.get("id", "")
        except AttributeError:
            continue  # already decomposed as child of an earlier match
        if _NOISE_ATTRS.search(cls) or _NOISE_ATTRS.search(tid):
            tag.decompose()

    lines = []

    # 3. Extract tables as structured text before prose — avoids cells being
    #    duplicated in the prose walk below.
    for table in soup.find_all("table"):
        ttext = _table_to_text(table)
        if ttext:
            lines.append(ttext)
        table.decompose()

    # 4. Find best content container
    body = None
    for method, args, kwargs in _CONTENT_SELECTORS:
        body = getattr(soup, method)(*args, **kwargs)
        if body:
            break
    if not body:
        body = soup.body or soup

    # 5. Walk prose elements
    for element in body.descendants:
        if element.name in ("h1", "h2", "h3", "h4", "p", "li"):
            text = element.get_text(" ", strip=True)
            if text and len(text) > 15:
                lines.append(text)

    seen, unique = set(), []
    for line in lines:
        if line not in seen:
            seen.add(line)
            unique.append(line)

    text = "\n".join(unique)

    # 6. trafilatura fallback if BS4 yield is thin
    if len(text.strip()) < 300:
        try:
            import trafilatura
            # Feed the already-cleaned soup (chrome/nav/noise stripped above), NOT raw html —
            # trafilatura on raw html reintroduces nav/header boilerplate on thin pages.
            traf = trafilatura.extract(
                str(soup),
                include_comments=False,
                include_tables=True,
                no_fallback=False,
            )
            if traf and len(traf.strip()) > len(text.strip()):
                text = traf
        except Exception:
            pass

    return title, text


def execute(url: str, max_chars: int = 3000, _fallback: bool = True) -> str:
    """
    Fetch a URL, with API fallback if direct fetch fails.

    Args:
        url: URL to fetch
        max_chars: Max characters to return
        _fallback: If True and direct fetch fails, try search API as backup
    """
    import random

    url = _normalise_url(url)

    ssrf_err = _check_ssrf(url)
    if ssrf_err:
        return ssrf_err

    _parsed = urlparse(url)
    if any(d in (_parsed.netloc + _parsed.path) for d in _JS_DOMAINS):
        pw_text = _playwright_fallback(url, max_chars, timeout_s=8.0)
        if pw_text:
            return f"URL: {url}\n[browser-rendered]\n{pw_text[:max_chars]}"
        # Playwright unavailable or failed — fall through to httpx

    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }

    try:
        resp = _get_session().get(url, headers=headers)
        redirect_count = 0
        while resp.status_code in (301, 302, 303, 307, 308) and redirect_count < 10:
            location = resp.headers.get("Location") or resp.headers.get("location")
            if not location:
                break
            from urllib.parse import urljoin
            redirect_url = urljoin(url, location)
            ssrf_err = _check_ssrf(redirect_url)
            if ssrf_err:
                return ssrf_err
            url = redirect_url
            resp = _get_session().get(url, headers=headers)
            redirect_count += 1
        resp.raise_for_status()
    except httpx.ConnectError as e:
        # SSL errors used to retry with verify=False — removed for security.
        # Fall through to Tavily so the user still gets an answer.
        if _fallback:
            return _api_fallback(url, str(e))
        return f"[fetch_failed] {e}"
    except httpx.TimeoutException:
        if _fallback:
            return _api_fallback(url, "timeout")
        return f"[fetch_failed] Timed out fetching {url} (>{_TIMEOUT}s)"
    except httpx.HTTPStatusError as e:
        if _fallback and e.response.status_code >= 400:
            return _api_fallback(url, f"HTTP_{e.response.status_code}")
        return f"[fetch_failed] HTTP {e.response.status_code} from {url}"
    except Exception as e:
        if _fallback:
            return _api_fallback(url, str(e))
        return f"[fetch_failed] {e}"

    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = resp.json()
        except Exception:
            try:
                data = json.loads(resp.text)
            except Exception:
                data = None
        if data is not None:
            pretty = json.dumps(data, indent=2, ensure_ascii=False)[:max_chars]
            if len(json.dumps(data, ensure_ascii=False)) > max_chars:
                pretty = pretty.rsplit("\n", 1)[0] + "\n[...truncated]"
            return f"URL: {url}\nContent-Type: {content_type}\n\n{pretty}"
        if _fallback:
            return _api_fallback(url, f"content-type: {content_type}")
        return f"[fetch_failed] Unsupported content-type: {content_type}"

    if "text/html" not in content_type and "text/plain" not in content_type:
        if _fallback:
            return _api_fallback(url, f"content-type: {content_type}")
        return f"[fetch_failed] Unsupported content-type: {content_type}"

    raw_html = resp.text
    title, text = _extract_text(raw_html, url=url)

    # Detect SPA: marker present or content suspiciously thin relative to page size
    spa_detected = any(m in raw_html for m in _SPA_MARKERS)
    content_thin = len(text.strip()) < 300

    if spa_detected or content_thin:
        pw_text = _playwright_fallback(url, max_chars, timeout_s=4.0)
        if pw_text and len(pw_text.strip()) > len(text.strip()):
            parts = [f"URL: {url}"]
            if title:
                parts.append(f"Title: {title}")
            trimmed = pw_text[:max_chars]
            if len(pw_text) > max_chars:
                trimmed = trimmed.rsplit("\n", 1)[0] + "\n[...truncated]"
            parts.append(f"\n[browser-rendered]\n{trimmed}")
            return "\n".join(parts)
        note = "[fetch_note] SPA/thin content detected — text may be incomplete.\n\n"
    else:
        note = ""

    if not text.strip():
        if _fallback:
            return _api_fallback(url, "no text extracted")
        return f"[fetch_failed] No readable text found at {url}"

    trimmed = text[:max_chars]
    if len(text) > max_chars:
        trimmed = trimmed.rsplit("\n", 1)[0] + "\n[...truncated]"

    parts = [f"URL: {url}"]
    if title:
        parts.append(f"Title: {title}")
    parts.append(f"\n{note}{trimmed}")

    return "\n".join(parts)


def _playwright_fallback(url: str, max_chars: int = 3000, timeout_s: float = 4.0) -> str | None:
    """
    Navigate with Playwright and extract rendered text.
    Returns text string or None on failure/unavailability.
    Uses trylock — skips if another thread is already in Playwright.
    """
    if not _playwright_in_flight.acquire(blocking=False):
        return None  # another thread is using Playwright, skip
    try:
        from tools.browser import browser_navigate, browser_get_content
        nav_timeout_ms = int(timeout_s * 600)  # 60% of budget for navigation
        nav_result = browser_navigate(url, timeout=nav_timeout_ms)
        if "[browser error]" in nav_result or "[error]" in nav_result:
            return None
        content = browser_get_content(selector="", max_chars=max_chars)
        if not content or "[browser error]" in content or len(content.strip()) < 100:
            return None
        return content.strip()
    except Exception:
        return None
    finally:
        _playwright_in_flight.release()


def _api_fallback(url: str, reason: str) -> str:
    """
    When direct fetch fails, try Tavily URL extract (not a search query).
    If that also fails, return failure — model should use browser_navigate.
    """
    import os

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
        resp = client.extract(urls=[url])
        results = resp.get("results", [])
        if results and results[0].get("raw_content"):
            content = results[0]["raw_content"][:2500]
            return f"[fetch_fallback] Direct fetch failed ({reason}). Extracted via Tavily:\n\n{content}"
    except Exception:
        pass

    return (
        f"[fetch_failed] Could not retrieve {url} ({reason}). "
        "Use browser_navigate to load this page in the browser."
    )
