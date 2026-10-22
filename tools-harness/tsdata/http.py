"""Shared HTTP plumbing for the tsdata sources.

Two things every source needs and that are easy to get wrong ad-hoc:

* **A real User-Agent.** Several hosts (Wikimedia, NOAA, US government APIs)
  reject the default `python-httpx/0.28` string with 403. We identify
  ourselves properly.
* **A disk cache.** Re-harvesting is common (we iterate on parsers), and
  hammering public APIs during development is both rude and rate-limit
  prone. Responses are cached under ``<lake>/raw/<sha256>`` with the URL
  stored alongside so a cache hit can be traced back to its origin.

A deliberately small retry policy: exponential backoff, respect
``Retry-After``, and retry broadly on transient failures — including
compressed/corrupt responses that some CDNs deliver. Re-running a harvest is
cheap because successful 200s are cached on disk.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from threading import Lock
from typing import Any

import httpx

USER_AGENT = (
    "clixen-tsdata/0.1 (research data collection; "
    "+https://github.com/intelogroup/clixen)"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

BROWSER_HEADERS = {
    **DEFAULT_HEADERS,
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

RETRY_STATUS = {429, 500, 502, 503, 504}
MIN_REQUEST_INTERVAL = 0.25
#: Hard ceiling on any single backoff sleep, so one throttled host cannot
#: stall an entire multi-source harvest.
MAX_BACKOFF = 8.0

#: Per-host minimum interval between requests (seconds). Hosts that throttle
#: aggressively get a wider spacing so a multi-symbol harvest does not trip
#: the limiter. Anything unlisted falls back to `MIN_REQUEST_INTERVAL`.
HOST_INTERVAL = {
    "query2.finance.yahoo.com": 1.5,
    "query1.finance.yahoo.com": 1.5,
    "hn.algolia.com": 0.5,
    "data-api.ecb.europa.eu": 0.5,
}


def _host_of(url: str) -> str:
    """Extract the lowercase host from a URL (best effort)."""
    try:
        from urllib.parse import urlparse
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


class Fetcher:
    """Cached HTTP GET with rate-limit, retry/backoff."""

    def __init__(self, cache_dir: str | Path, timeout: float = 30.0,
                 max_retries: int = 5):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: httpx.Client | None = None
        self._lock = Lock()
        self._last_request: dict[str, float] = {}
        #: Hosts that answered 429 to our long Chrome UA. Once a host has shown
        #: that it throttles on the UA string, every later request to it
        #: goes out with the short UA straight away.
        self._ua_downgraded: set[str] = set()
        self._last_response_content: bytes = b""
        self._last_response_url: str = ""
        self._last_response_status: int = 0
        self._last_response_headers: dict[str, str] = {}

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout, follow_redirects=True, headers=DEFAULT_HEADERS)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    def _thin(self, url: str = "") -> None:
        """Enforce a minimum interval between requests, per host."""
        host = _host_of(url)
        interval = HOST_INTERVAL.get(host, MIN_REQUEST_INTERVAL)
        with self._lock:
            last = self._last_request.get(host, 0.0)
            now = time.time()
            gap = now - last
            if gap < interval:
                time.sleep(interval - gap)
            self._last_request[host] = time.time()

    def _cache_paths(self, url: str, params: dict | None) -> tuple[Path, Path]:
        key = hashlib.sha256(
            (url + json.dumps(params or {}, sort_keys=True)).encode()
        ).hexdigest()
        blob = self.cache_dir / key[:2] / f"{key}.body"
        meta = self.cache_dir / key[:2] / f"{key}.meta.json"
        return blob, meta

    def _backoff(self, attempt: int, payload: Any) -> None:
        """Sleep before the next attempt.

        Capped: a throttled host must not be able to stall a whole harvest.
        `Retry-After` is honoured but clipped to `MAX_BACKOFF` seconds.
        """
        if isinstance(payload, httpx.Response):
            wait = payload.headers.get("Retry-After")
            try:
                delay = min(float(wait), MAX_BACKOFF) if wait else 2 ** attempt
            except ValueError:
                delay = 2 ** attempt
        else:
            delay = 2 ** attempt
        time.sleep(max(delay, 0.25))

    def _write_cache(self, url: str, params: dict | None) -> None:
        blob, meta = self._cache_paths(url, params)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(self._last_response_content)
        # `content` is the *decoded* body, so the content-encoding header must
        # not be replayed — httpx would try to decompress it a second time and
        # blow up with "brotli: decoder failed" / "zlib error" on cache hits.
        headers = {
            k: v for k, v in self._last_response_headers.items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
        }
        meta.write_text(json.dumps({
            "url": self._last_response_url,
            "status_code": self._last_response_status,
            "headers": headers,
            "fetched_at": time.time(),
        }))

    # ------------------------------------------------------------------
    def get(self, url: str, params: dict | None = None,
            headers: dict | None = None, use_cache: bool = True,
            force: bool = False) -> httpx.Response:
        """GET with disk cache, rate-limit, and retry/backoff."""
        blob, meta_p = self._cache_paths(url, params)
        if use_cache and not force and blob.exists():
            meta = json.loads(meta_p.read_text())
            headers = {
                k: v for k, v in (meta.get("headers") or {}).items()
                if k.lower() not in ("content-encoding", "content-length",
                                     "transfer-encoding")
            }
            return httpx.Response(
                status_code=meta["status_code"],
                content=blob.read_bytes(),
                headers=headers,
                request=httpx.Request("GET", url),
            )

        last_exc: Exception | None = None
        ua_downgraded = False
        host = _host_of(url)
        for attempt in range(self.max_retries):
            self._thin(url)
            req_headers = headers
            if headers and host in self._ua_downgraded:
                req_headers = {**headers, "User-Agent": "Mozilla/5.0"}
            try:
                r = self.client.get(url, params=params, headers=req_headers)
            except httpx.HTTPError as e:
                last_exc = e
                self._backoff(attempt, e)
                continue
            if r.status_code in RETRY_STATUS:
                # Some CDNs (Yahoo Finance in particular) throttle the *long*
                # Chrome User-Agent string specifically and happily serve a
                # short one. Downgrading the UA once is far cheaper than
                # burning the whole retry budget on a 429 we caused.
                if (r.status_code == 429 and headers and not ua_downgraded
                        and (headers.get("User-Agent") or "").startswith("Mozilla/5.0 (")):
                    ua_downgraded = True
                    self._ua_downgraded.add(host)
                    headers = {**headers, "User-Agent": "Mozilla/5.0"}
                    continue
                self._backoff(attempt, r)
                continue
            try:
                if use_cache and r.status_code == 200:
                    self._last_response_content = r.content
                    self._last_response_url = str(r.url)
                    self._last_response_status = r.status_code
                    self._last_response_headers = dict(r.headers)
                    self._write_cache(url, params)
            except Exception as e:  # compressed/corrupt response — retry
                last_exc = e
                self._backoff(attempt, e)
                continue
            return r
        raise last_exc or httpx.HTTPError(f"GET failed: {url}")

    def get_json(self, url: str, **kw: Any) -> Any:
        """GET + parse JSON.

        Deliberately does NOT add a second retry loop around `get()`: `get()`
        already retries transient failures (including 429) with backoff, and
        nesting the two multiplied the attempt count (5x5) while Yahoo
        throttled us, turning one throttled symbol into a multi-minute stall.
        Only a malformed body is worth retrying here.
        """
        last_exc: Exception | None = None
        for attempt in range(2):
            r = self.get(url, **kw)
            r.raise_for_status()
            try:
                return r.json()
            except Exception as e:  # truncated / corrupt body
                last_exc = e
                self._backoff(attempt, e)
        raise last_exc or httpx.HTTPError(f"parse failed: {url}")

    def get_text(self, url: str, **kw: Any) -> str:
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.text
