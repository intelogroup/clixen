# Web Search

```
tools/websearch.py                  668 lines — guard→rewrite→search→rerank→summarize→finalize
tools/searxng_search.py             125 lines — SearXNG adapter (70+ engines, Google included) + DDG fallback
tools/ddg_search.py                 296 lines — DuckDuckGo free search fallback
tools/search_agentic/_scoring.py    734 lines — scoring helpers (freshness/authority/domain-allow)
tools/search_agentic/_summarize.py  866 lines — rerank_items() + summarize_with_model()
```

Backend: SearXNG self-hosted in Docker (`docker run -d --name searxng -p 8888:8080 searxng/searxng`).
Parallel search: SearXNG (70+ engines, Google included) + DDG run simultaneously, merged and deduplicated.
No API keys required. DDG adds ~20-30% unique results per query, enriching the reranker.

Summarizer: `gemma4:12b-mlx` (primary; `gemma4:e2b` is warmed but not primary — see CLAUDE.md model roster).
Format hints auto-detected: explain/compare/list/multi/benefits.

See the root CLAUDE.md "Web Search Architecture" section for the current pipeline flow, Chinese-query branch, and bug-fix history — that section is kept in the always-loaded file since web search is touched often.

## Discovery APIs (academic/corporate/geo sources)

`tools/discovery_sources.py` — Wikidata, OpenAlex, Crossref, OpenCorporates, SEC EDGAR,
Nominatim, Wikipedia, GDELT. Smoke-tested in `tests/test_new_sources.py`.

Crossref politeness (verified live): polite-pool identification requires `mailto=` as a
**query param** (`&mailto=...`) or inside the User-Agent string — a `Mailto:` HTTP header is
NOT recognized, so requests hit the public pool and burst-429. Both Crossref and OpenAlex
accept the `mailto` param. `_get_retry()` backoff on 429/5xx honors `Retry-After`.
