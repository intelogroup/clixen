from tools.search_agentic import rerank_items
from tools.search_result import SearchSnippet


def test_rerank_items_works_without_model_reranker():
    items = [
        SearchSnippet(
            title="Old unrelated market wrap",
            url="https://example.com/old",
            snippet="General market commentary with little about tariffs.",
            published_date="2026-03-01",
            source="tavily",
        ),
        SearchSnippet(
            title="Stocks fall after tariff escalation",
            url="https://example.com/tariffs",
            snippet="US stocks dropped after new tariff fears hit markets on April 9, 2026.",
            published_date="2026-04-09",
            source="tavily",
        ),
    ]

    ranked = rerank_items("why did stocks drop on april 9 2026", items, limit=2)
    assert ranked[0].url == "https://example.com/tariffs"


def test_rerank_items_filters_denied_domains():
    items = [
        SearchSnippet(
            title="Pinned image result",
            url="https://pinterest.com/example",
            snippet="A short snippet that should be denied entirely.",
            published_date="2026-04-01",
            source="tavily",
        ),
        SearchSnippet(
            title="Reuters market report",
            url="https://reuters.com/markets/tariffs",
            snippet="Tariff escalation weighed on US equities in the latest session.",
            published_date="2026-04-09",
            source="tavily",
        ),
    ]

    ranked = rerank_items("latest tariff market reaction", items, limit=2, domain="finance")
    assert all("pinterest.com" not in item.url for item in ranked)
    assert ranked[0].url == "https://reuters.com/markets/tariffs"
