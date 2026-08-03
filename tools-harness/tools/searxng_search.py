"""
SearXNG web search adapter — zero-cost, 70+ engines aggregated, fully local when available.

Precedence:
  1. Local SearXNG instance (SEARXNG_URL env var, default http://localhost:8888)
  2. DuckDuckGo free search fallback (no API key needed)

To run SearXNG locally:
    docker run -d --name searxng -p 8888:8888 -v searxng-settings:/etc/searxng searxng/searxng
    docker exec searxng sed -i 's/formats: [html]/formats: [html, json]/' /etc/searxng/settings.yml
    docker restart searxng
"""

import os
import logging
from tools.search_result import SearchResult

log = logging.getLogger(__name__)

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8888")

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
            resp = client.get(f"{SEARXNG_URL}/search", params=params)

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
    """Search via SearXNG with DDG fallback."""
    # Try SearXNG first
    result = _search_searxng(query, categories=categories, time_range=time_range, max_results=max_results)
    if result and result.ok:
        log.debug("SearXNG: %d results for %r", len(result.items or []), query[:60])
        return result

    # Fall back to DuckDuckGo
    log.debug("SearXNG unavailable, falling back to DDG for %r", query[:60])
    return _fallback_ddg(query, time_range=time_range, max_results=max_results)
