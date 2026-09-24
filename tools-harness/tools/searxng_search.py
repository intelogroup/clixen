"""
SearXNG web search adapter — zero-cost, 70+ engines aggregated, fully local when available.

Precedence:
  1. Local SearXNG instance (SEARXNG_URL env var, default http://localhost:8888 —
     this host overrides to :8889 via tools-harness/.env because 8888 is occupied
     by an unrelated process; verified JSON API live 2026-09-24)
  2. DuckDuckGo free search fallback (no API key needed)

To run SearXNG locally:
    docker run -d --name searxng -p 8888:8080 -v searxng-settings:/etc/searxng searxng/searxng
    # image listens on container port 8080, not 8888 — mapping -p 8888:8888 leaves
    # nothing behind the host port (connection reset, not refused; easy to misdiagnose).
    # current settings.yml template also has no "formats:" line for sed to match —
    # append the block instead:
    docker exec searxng sh -c 'cat >> /etc/searxng/settings.yml << "EOF"

search:
  formats:
    - html
    - json
EOF'
    docker restart searxng
"""

import os
import logging
from tools.search_result import SearchResult

log = logging.getLogger(__name__)

# 2026-09-24 (L6): read at call time, not import time — websearch.py imports this
# module at its own top (line ~27) but only runs load_dotenv() lazily inside
# _search(), so an import-time read here permanently latched the 8888 default in
# any process that didn't load .env before import (pytest, and any future caller
# that imports early). Lazy read picks up SEARXNG_URL whenever it appears.
def _searxng_url() -> str:
    return os.environ.get("SEARXNG_URL", "http://localhost:8888")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "searxng_search",
        "description": (
            "Search the web via SearXNG (70+ engines aggregated, no API keys, fully private). "
            "Use for any web query — news, facts, code, science, prices, scores."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "categories": {
                    "type": "string",
                    "description": "Search categories: general, news, science, it, social+media, images, video, files, music",
                    "default": "general",
                },
                "time_range": {
                    "type": "string",
                    "description": "Time filter: day, month, year. Omit for all time.",
                    "enum": ["day", "month", "year"],
                },
            },
            "required": ["query"],
        },
    },
}


def _search_searxng(query: str, categories: str = "general", time_range: str = "", max_results: int = 10) -> SearchResult | None:
    """Try SearXNG local instance. Returns None if unavailable."""
    try:
        import httpx

        params = {"q": query, "format": "json", "categories": categories}
        if time_range:
            params["time_range"] = time_range

        with httpx.Client(timeout=8) as client:
            resp = client.get(f"{_searxng_url()}/search", params=params)

        if resp.status_code != 200:
            return None

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None

        items = []
        for r in results[:max_results]:
            items.append({
                "title": r.get("title", "") or "",
                "url": r.get("url", "") or "",
                "snippet": (r.get("content", "") or "")[:400],
                "published_date": r.get("publishedDate") or "",
                "source": f"searxng:{','.join(r.get('engine', ['?'])) if isinstance(r.get('engine'), list) else r.get('engine', '?')}",
            })

        content_parts = [f"- {i['title']} ({i['url']})\n  {i['snippet']}" for i in items]
        return SearchResult(
            content="\n\n".join(content_parts),
            ok=True,
            source="searxng",
            query=query,
            items=items,
        )
    except Exception:
        return None


def _fallback_ddg(query: str, time_range: str = "", max_results: int = 10) -> SearchResult:
    """Fall back to DuckDuckGo free search."""
    from tools.ddg_search import execute as ddg_execute

    try:
        timelimit = None
        if time_range == "day":
            timelimit = "d"
        elif time_range == "month":
            timelimit = "m"
        elif time_range == "year":
            timelimit = "y"
        return ddg_execute(query, max_results=max_results, timelimit=timelimit)
    except Exception as e:
        return SearchResult(
            content="", ok=False, error=f"DDG fallback failed: {e}", source="searxng", query=query
        )


def execute(query: str, categories: str = "general", time_range: str = "", max_results: int = 10) -> SearchResult:
    """Search via SearXNG, then Exa/Tavily, then DDG as a last resort.

    The local SearXNG and DDG tiers are optional legacy backends. In the
    supported Python 3.14 runtime DDG is intentionally disabled, so API-backed
    search must be attempted before reaching that fallback.
    """
    # Try SearXNG first
    result = _search_searxng(query, categories=categories, time_range=time_range, max_results=max_results)
    if result and result.ok:
        log.debug("SearXNG: %d results for %r", len(result.items or []), query[:60])
        return result

    # API-backed web search is the supported fallback when local SearXNG is
    # absent. websearch._search is Exa-first, Tavily-second, and only then
    # considers the legacy scraping backends.
    try:
        from tools.websearch import _search as api_search
        api_result = api_search(query, time_range=time_range)
        if api_result and api_result.ok and api_result.items:
            api_result.items = api_result.items[:max_results]
            log.debug("API web search (%s): %d results for %r", api_result.source,
                      len(api_result.items), query[:60])
            return api_result
        log.warning("API web search returned no results for %r: %s", query[:60],
                    api_result.error if api_result else "no result")
    except Exception as exc:
        log.warning("API web search failed for %r: %s", query[:60], exc)

    # Last resort only: DDG is unavailable on Python 3.14+, but retaining this
    # branch preserves compatibility for older runtimes/configurations.
    log.debug("SearXNG/API search unavailable, falling back to DDG for %r", query[:60])
    return _fallback_ddg(query, time_range=time_range, max_results=max_results)
