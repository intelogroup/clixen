import pytest
from tools.query_guard import check_all as guard_check
from tools.search_agentic import _evidence_block
from tools.search_result import SearchSnippet
from tools.websearch import search

def test_assumption_query_guard_whitespace_only():
    # Assumption: Whitespace-only queries are blocked by query guard
    assert guard_check("   \n\t   ") == "Please enter a valid search query."

def test_assumption_query_guard_non_alphanumeric():
    # Assumption: Non-alphanumeric queries are blocked by query guard
    assert guard_check("@@@@@@") == "Please enter a search query with valid text or characters."
    assert guard_check("!$#%^&*()") == "Please enter a search query with valid text or characters."

def test_query_guard_allows_non_latin_script():
    # 2026-07-10: the junk-query check used r"[a-zA-Z0-9]", which rejected
    # every purely non-Latin-script query as garbage -- bare Chinese/Japanese
    # queries with zero ASCII characters were blocked outright even though
    # they're legitimate (this repo has a dedicated Chinese-query search
    # path in tools/agent_reach.py). Fixed via Unicode-aware \w.
    assert guard_check("今天天气怎么样") is None
    assert guard_check("こんにちは元気ですか") is None

def test_assumption_evidence_block_snippet_capping():
    # Assumption: Each snippet is capped at max_snippet length
    items = [
        SearchSnippet(url="https://a.com", title="Title A", snippet="X" * 1000)
    ]
    block = _evidence_block(items, max_snippet=300, max_total=2000)
    # Check that snippet content contains "X" but not more than 300
    assert "Title A" in block
    assert "X" * 300 in block
    assert "X" * 301 not in block

def test_assumption_evidence_block_total_truncation():
    # Assumption: When total size exceeds max_total, subsequent items are dropped entirely
    items = [
        SearchSnippet(url="https://a.com", title="Title A", snippet="X" * 200),
        SearchSnippet(url="https://b.com", title="Title B", snippet="Y" * 200),
    ]
    # Total space for first entry is ~250 chars. If max_total is 300, the first fits but not the second.
    block = _evidence_block(items, max_snippet=300, max_total=300)
    assert "Title A" in block
    assert "Title B" not in block

def test_assumption_academic_temporal_query(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    from tools.search_agentic import _is_temporal
    from tools.websearch import _ACADEMIC_RE, _search
    
    query = "latest machine learning paper on transformers"
    
    # 1. Assert temporal and academic detection work on this query
    assert _is_temporal(query) is True
    assert _ACADEMIC_RE.search(query) is not None
    
    # 2. Record which backends are called and with what arguments
    called_backends = {}
    
    def mock_searxng(q, time_range, **kwargs):
        called_backends["searxng"] = (q, time_range)
        return SearchResult(content="", ok=True, source="searxng", query=q, items=[])
        
    def mock_ddg(q, max_results, timelimit, **kwargs):
        called_backends["ddg"] = (q, timelimit)
        return SearchResult(content="", ok=True, source="ddg", query=q, items=[])
        
    def mock_arxiv(q, **kwargs):
        called_backends["arxiv"] = q
        return SearchResult(content="", ok=True, source="arxiv", query=q, items=[])
        
    def mock_pubmed(q, **kwargs):
        called_backends["pubmed"] = q
        return SearchResult(content="", ok=True, source="pubmed", query=q, items=[])

    monkeypatch.setattr("tools.websearch._search_searxng", mock_searxng)
    monkeypatch.setattr("tools.ddg_search.execute", mock_ddg)
    monkeypatch.setattr("tools.arxiv_search.execute", mock_arxiv)
    monkeypatch.setattr("tools.pubmed.execute", mock_pubmed)

    # Run parallel search
    res = _search(query, time_range="year")
    
    # Assert all 4 backends were invoked
    assert "searxng" in called_backends
    assert "ddg" in called_backends
    assert "arxiv" in called_backends
    assert "pubmed" in called_backends
    
    # Assert time range/limit was correctly propagated
    assert called_backends["searxng"] == (query, "year")
    assert called_backends["ddg"] == (query, "y")


def test_assumption_query_guard_sports_bare_pronoun():
    # 1. Bare sports pronoun blocks
    res = guard_check("will they play today")
    assert res == "Which team or player are you asking about? Please give me the name."


def test_assumption_query_guard_food_cravings():
    # 2. Food craving blocks when missing location/service context
    res = guard_check("I crave something to eat")
    assert res == "What are you in the mood for, and where are you? I can check DoorDash, Uber Eats, or find nearby restaurants."


def test_assumption_query_guard_data_analysis():
    # 3. Data operation blocks when missing file reference
    res = guard_check("plot variables and compare groups")
    assert res == "Which data file would you like me to analyze? Please provide the file path (CSV, JSON, Parquet, Excel, etc.) or tell me what you're looking for."


def test_assumption_query_guard_ambiguous_named_entity():
    # 4. Ambiguous bare terms block
    res = guard_check("Mercury")
    assert "Which Santiago?" not in res
    assert "Could you clarify what you mean? Mercury" in res


def test_assumption_query_guard_ambiguous_entity_phrasings():
    # 4b. Regression: trailing punctuation and lead-in fillers must not bypass the
    # entity guard ("Mercury?" / "tell me about Mercury" used to slip through).
    for q in ("Mercury?", "tell me about Mercury", "tell me about Mercury.", "what is mercury"):
        assert "clarify" in (guard_check(q) or "").lower(), q
    # ...but disambiguating context (or a genuinely different subject) must NOT trigger it.
    for q in ("tell me about the planet Mercury", "mercury poisoning symptoms in adults"):
        res = guard_check(q) or ""
        assert "Could you clarify what you mean? Mercury" not in res, q


def test_assumption_query_guard_ambiguous_toponym():
    # 5. Ambiguous city names block
    res = guard_check("I live in Santiago")
    assert "Which Santiago?" in res


def test_assumption_is_temporal_historical_dates():
    # 6. Historical queries are NOT temporal
    from tools.search_agentic import _is_temporal
    assert _is_temporal("who was king of england in 1540") is False
    assert _is_temporal("history of the Roman Empire") is False


def test_assumption_websearch_academic_trigger():
    # 7. Academic regex triggers on research terms
    from tools.websearch import _ACADEMIC_RE
    assert _ACADEMIC_RE.search("neural network architecture paper") is not None
    assert _ACADEMIC_RE.search("side effects of metformin") is not None


def test_assumption_decoy_domain_filtering():
    # 8. Denied domains are filtered by reranking
    from tools.search_agentic import rerank_items
    items = [
        SearchSnippet(url="https://pinterest.com/pin/1", title="Pinterest Pin", snippet="Check out this pin"),
        SearchSnippet(url="https://reuters.com/news", title="Reuters News", snippet="World news coverage"),
    ]
    ranked = rerank_items("global news", items, limit=5, domain="news")
    urls = [i.url for i in ranked]
    assert "https://reuters.com/news" in urls
    assert not any("pinterest.com" in u for u in urls)


def test_assumption_freshness_decay_boundaries():
    # 9. Freshness decay returns 2.0 on 0 days, 0.0 on 60+ days
    from tools.search_agentic import _freshness_decay
    assert _freshness_decay(0) == 2.0
    assert _freshness_decay(60) == 0.0
    assert _freshness_decay(90) == 0.0


def test_assumption_rewrite_resolves_today_deterministically():
    # 10. The deterministic date pass resolves "today" to the real current date.
    # A 1-word query reduces to a 3-token date and returns before any LLM call.
    import datetime as _dt
    from tools.websearch import _rewrite_query
    out = _rewrite_query("today").lower()
    today = _dt.datetime.now().strftime('%B %d, %Y').lower()
    assert today in out, out
    assert "today" not in out, out


def test_assumption_rewrite_prompt_has_no_hardcoded_example_date():
    # 11. Regression: a hardcoded example date ("june 24 2026") in the LLM rewrite
    # prompt leaked into qwen3.5:4b output and overwrote the real query date
    # (asked "today" = June 27, agent answered June 24). The prompt must build the
    # date via strftime only — never pin a literal calendar date as an example.
    import inspect, re as _re
    from tools.websearch import _rewrite_query
    src = inspect.getsource(_rewrite_query).lower()
    months = "january|february|march|april|may|june|july|august|september|october|november|december"
    assert not _re.search(rf"\b(?:{months})\s+\d{{1,2}}\b", src), \
        "hardcoded example date re-introduced in the rewrite prompt"


def test_assumption_clean_handling_nulls():
    # 10. clean utility parses empty strings and multi-newlines cleanly
    from tools.search_agentic import _clean
    assert _clean("") == ""
    assert _clean(None) == ""
    assert _clean("  Line 1  \n\n  Line 2  ") == "Line 1 Line 2"


def test_assumption_websearch_tavily(monkeypatch):
    # Exa runs unconditionally alongside Tavily (not just as an on-failure
    # fallback, see CLAUDE.md "Web Search Architecture"), and DDG runs in the
    # same fallback tier as SearXNG — both must be silenced or their live
    # results leak into the merged item list alongside Tavily's mocked one.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "mock_tavily_key")

    class MockTavilyClient:
        def __init__(self, api_key):
            assert api_key == "mock_tavily_key"
        def search(self, query, **kwargs):
            return {
                "results": [
                    {"title": "Tavily Title", "url": "https://tavily.com", "content": "Tavily Content"}
                ]
            }

    monkeypatch.setattr("tavily.TavilyClient", MockTavilyClient)
    monkeypatch.setattr("tools.websearch._search_searxng", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "tools.ddg_search.execute",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("DDG silenced for isolation")),
    )

    def mock_summarize(query, items, **kwargs):
        assert len(items) == 1
        assert items[0].url == "https://tavily.com"
        return "Synthesized Tavily Answer"
    monkeypatch.setattr("tools.search_agentic.summarize_with_model", mock_summarize)

    res = search("when is the next launch date")
    assert res == "Synthesized Tavily Answer"



