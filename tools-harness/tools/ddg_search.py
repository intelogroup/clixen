"""
DuckDuckGo free search fallback — no API key required.

Uses the ddgs package (formerly duckduckgo-search) to query DDG's unofficial API.
Zero cost, low rate limits, but less refined results than paid APIs.

Install: pip install ddgs
"""

import re

from tools.search_result import SearchResult

_HEALTH_EPI_RE = re.compile(
    r"\b(prevalence|incidence|cases?|deaths?|mortality|morbidity|statistics|"
    r"outbreak|epidemic|pandemic|spread|infection\s*rate|case\s*count|"
    r"hospitalization|vaccination\s*rate|active\s*cases?)\b",
    re.I,
)

_HEALTH_AUTHORITY_SITES = (
    "data.who.int",
    "who.int",
    "cdc.gov",
    "ourworldindata.org",
)
_HEALTH_DISEASE_RE = re.compile(
    r"\b(covid(?:-?19)?|coronavirus|flu|influenza|rsv|mpox|measles|ebola|norovirus)\b",
    re.I,
)
_HEALTH_SIGNAL_RE = re.compile(
    r"\b(prevalence|incidence|cases?|deaths?|mortality|morbidity|statistics|"
    r"surveillance|tracker|dashboard|weekly|update)\b",
    re.I,
)

# Niche/fan press sites DDG doesn't index well — supplement with site:reddit.com
# to surface crowd-sourced discussion that links to authoritative sources.
_NICHE_ANNOUNCEMENT_RE = re.compile(
    r"\b(disney|marvel|pixar|star wars|lucasfilm|indiana jones|"
    r"cinemacon|cannes|comic-?con|sxsw|tribeca)\b",
    re.I,
)


def _get_optimal_max_results(query: str, intent: str = "", domain: str = "") -> int:
    """Determine optimal max_results based on query intent for faster DDG."""
    # Temporal/live queries need speed + freshness over volume
    if intent in ("live", "recent") or domain in ("news", "finance", "crypto"):
        return 2  # Faster: 0.75s-1.39s, enough for trending info

    # Recommendation queries benefit from more options
    if intent == "recommendation" or domain in ("electronics", "movie", "reviews"):
        return 3  # Balanced: 0.75s, good coverage

    # Definition/factual queries need accuracy over volume
    if intent in ("definition", "factual_historical", "comparison"):
        return 2  # Faster: minimal duplication

    # Default: balanced approach
    return 3


def _ddg_queries(query: str, intent: str = "", domain: str = "") -> list[str]:
    """Build DDG query variants using supported search operators."""
    queries = [query.strip()]
    if domain == "health" and _HEALTH_EPI_RE.search(query):
        core_query = _health_epi_core_query(query)
        queries = [*_health_authority_queries(core_query), query.strip()]
    # Niche entertainment/franchise announcements: add site:reddit.com variant
    # to surface crowd-sourced discussion that links to authoritative sources.
    if _NICHE_ANNOUNCEMENT_RE.search(query):
        queries.append(f"{query.strip()} site:reddit.com")
    # Deduplicate while preserving order
    out = []
    seen = set()
    for q in queries:
        if q and q not in seen:
            out.append(q)
            seen.add(q)
    return out


def _health_epi_core_query(query: str) -> str:
    """Condense a verbose health question to the disease + stats terms DDG responds to better."""
    disease_match = _HEALTH_DISEASE_RE.search(query)
    disease = disease_match.group(1).lower() if disease_match else "disease"
    signals = []
    for match in _HEALTH_SIGNAL_RE.findall(query):
        term = match.lower()
        if term not in signals:
            signals.append(term)
    if not signals:
        signals = ["cases", "statistics"]
    return " ".join([disease, *signals[:3]])


def _health_authority_queries(core_query: str) -> list[str]:
    return [
        f"{core_query} dashboard site:data.who.int inurl:cases",
        f"{core_query} epidemiological update site:who.int intitle:update",
        f"{core_query} surveillance site:cdc.gov intitle:surveillance",
        f"{core_query} site:ourworldindata.org inurl:coronavirus",
    ]


def _per_query_results(max_results: int, query_count: int) -> int:
    """Keep each variant small so expansion improves recall without bloating latency."""
    if query_count == 1:
        return max_results
    if query_count > 3:
        return 1
    return max(1, min(max_results, 3))


def _max_pool_size(max_results: int, query_count: int, per_query_results: int) -> int:
    if query_count > 1:
        return max_results
    return max(max_results, min(query_count * per_query_results, max_results * 3))


SCHEMA = {
    "type": "function",
    "function": {
        "name": "ddg_search",
        "description": (
            "Free web search via DuckDuckGo (no API key required). "
            "Supports time-based filtering. "
            "Best for: fallback when API keys are exhausted, basic queries, recent information. "
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
                    "description": "Number of results to return (1-5). Auto-optimized if omitted based on intent.",
                },
                "timelimit": {
                    "type": "string",
                    "description": "Time filter: 'day' (past 24h), 'week', 'month', 'year', or None (all time). Default: None.",
                    "enum": ["day", "week", "month", "year"],
                },
            },
            "required": ["query"],
        },
    },
}


def execute(
    query: str,
    max_results: int | None = None,
    timelimit: str | None = None,
    intent: str = "",
    domain: str = "",
) -> SearchResult:
    """
    Execute a DuckDuckGo search.

    Args:
        query: Search query string
        max_results: Max results to return (1-5). If None, auto-determined by intent/domain.
        timelimit: Time filter — 'd' (day), 'w' (week), 'm' (month), 'y' (year), or None
        intent: Query semantic intent (for optimization)
        domain: Query domain (for optimization)

    Returns:
        SearchResult with content ready for the model, or ok=False on error
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return SearchResult(
            content="",
            ok=False,
            error="ddgs not installed. Run: pip install ddgs",
            source="ddg",
            query=query,
        )

    # Auto-determine optimal max_results if not specified
    if max_results is None:
        max_results = _get_optimal_max_results(query, intent, domain)

    max_results = max(1, min(max_results, 5))
    # Map friendly names to ddgs codes
    timelimit_map = {"day": "d", "week": "w", "month": "m", "year": "y"}
    if timelimit in timelimit_map.values():
        timelimit_code = timelimit
    else:
        timelimit_code = timelimit_map.get(timelimit) if timelimit else None


    try:
        from tools.search_agentic import rerank_items

        ddgs = DDGS(timeout=4)
        merged_results = []
        seen_urls = set()
        queries = _ddg_queries(query, intent=intent, domain=domain)
        per_query_results = _per_query_results(max_results, len(queries))
        max_pool = _max_pool_size(max_results, len(queries), per_query_results)
        for ddg_query in queries:
            batch = list(
                ddgs.text(
                    ddg_query,
                    region="us-en",
                    safesearch="moderate",
                    max_results=per_query_results,
                    timelimit=timelimit_code,
                    backend="auto",
                )
            )
            for result in batch:
                href = (result.get("href", "") or "").strip()
                if not href or href in seen_urls:
                    continue
                merged_results.append(result)
                seen_urls.add(href)
                if len(merged_results) >= max_pool:
                    break
            if len(merged_results) >= max_pool:
                break
        normalized_results = [
            {
                "title": item.get("title", "") or "",
                "url": item.get("href", "") or "",
                "snippet": item.get("body", "") or "",
                "source": "ddg",
            }
            for item in merged_results
        ]
        results = [
            {
                "title": item.title,
                "href": item.url,
                "body": item.snippet,
            }
            for item in rerank_items(
                query,
                normalized_results,
                limit=max_results,
                domain=domain,
                intent=intent,
            )
        ]
    except Exception as e:
        error_msg = str(e)
        # DuckDuckGo rate-limits silently with an empty response on repeated calls
        if not error_msg or "no result" in error_msg.lower():
            error_msg = "DDG rate-limited or no results"
        return SearchResult(
            content="",
            ok=False,
            error=error_msg,
            source="ddg",
            query=query,
        )

    if not results:
        return SearchResult(
            content="",
            ok=False,
            error="No results",
            source="ddg",
            query=query,
        )

    # Format for display and reranking
    parts = []
    items = []
    for r in results:
        title = r.get("title", "") or ""
        url = r.get("href", "") or ""
        body = r.get("body", "") or ""

        # Trim long snippets
        if body and len(body) > 500:
            body = body[:500].rstrip() + "…"

        parts.append(f"- {title} ({url})\n  {body}")
        items.append(
            {
                "title": title,
                "url": url,
                "snippet": body,
                "source": "ddg",
            }
        )

    content = "\n\n".join(parts)
    return SearchResult(
        content=content,
        ok=True,
        source="ddg",
        query=query,
        items=items,
    )
