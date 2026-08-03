import os
import logging
from pathlib import Path
import requests
from dotenv import load_dotenv
from tools.search_result import SearchResult, SearchSnippet

log = logging.getLogger(__name__)

# Load environment variables
load_dotenv(Path(__file__).parent.parent / ".env")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "brave_search",
        "description": (
            "Search the web using Brave's independent index. "
            "Best for: current news, AI/tech releases, niche topics, evergreen questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (1-10)",
                    "default": 5,
                },
                "freshness": {
                    "type": "string",
                    "description": "Time filter: 'day', 'week', 'month', 'year'. Omit for all time.",
                    "enum": ["day", "week", "month", "year"],
                },
            },
            "required": ["query"],
        },
    },
}

def execute(query: str, max_results: int = 5, freshness: str | None = None) -> SearchResult:
    key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
    if not key:
        log.warning("brave_search: BRAVE_SEARCH_API_KEY not set")
        return SearchResult(
            content="",
            ok=False,
            error="BRAVE_SEARCH_API_KEY not set in environment",
            source="brave",
            query=query,
        )

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": key
    }
    
    # Map time filters to Brave's freshness parameter
    freshness_code = None
    if freshness:
        if freshness == "day":
            freshness_code = "pd"
        elif freshness == "week":
            freshness_code = "pw"
        elif freshness == "month":
            freshness_code = "pm"
        elif freshness == "year":
            freshness_code = "py"
        else:
            freshness_code = freshness

    params = {"q": query, "count": max_results}
    if freshness_code:
        params["freshness"] = freshness_code

    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params=params,
            headers=headers,
            timeout=8,
        )
        if r.status_code != 200:
            log.debug("brave_search: HTTP status %d", r.status_code)
            return SearchResult(
                content="",
                ok=False,
                error=f"Brave Search API returned HTTP {r.status_code}",
                source="brave",
                query=query,
            )
        data = r.json()
    except Exception as e:
        log.debug("brave_search: request failed: %s", e)
        return SearchResult(
            content="",
            ok=False,
            error=f"Brave Search request failed: {e}",
            source="brave",
            query=query,
        )

    web_results = data.get("web", {}).get("results", [])
    if not web_results:
        log.warning("brave_search: no results returned for query %r", query[:50])
        return SearchResult(
            content="",
            ok=False,
            error="No results from Brave Search",
            source="brave",
            query=query,
        )

    items = []
    parts = []
    for r_item in web_results[:max_results]:
        title = r_item.get("title", "") or ""
        url = r_item.get("url", "") or ""
        snippet = r_item.get("description", "").replace("<strong>", "").replace("</strong>", "") or ""
        published_date = r_item.get("age", "") or ""
        
        snippet_item = SearchSnippet(
            title=title,
            url=url,
            snippet=snippet,
            published_date=published_date,
            source="brave",
        )
        items.append(snippet_item)
        parts.append(f"- {title} ({url})\n  {snippet}")

    content = "\n\n".join(parts)
    return SearchResult(
        content=content,
        ok=True,
        source="brave",
        query=query,
        items=items,
    )
