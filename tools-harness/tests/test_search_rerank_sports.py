import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.search_agentic import rerank_items
from tools.search_result import SearchSnippet


def _item(url: str, title: str, snippet: str) -> SearchSnippet:
    return SearchSnippet(
        title=title,
        url=url,
        snippet=snippet,
        published_date="2026-04-01",
        source="test",
    )


def test_rerank_filters_low_quality_world_cup_roster_domains_when_reputable_sources_exist():
    query = "who are selected in the haiti soccer team for the world cup"
    items = [
        _item(
            "https://fifa-worldcup26.com/team/haiti/",
            "Haiti World Cup 2026 squad",
            "Haiti squad players for the world cup include several stars and selections.",
        ),
        _item(
            "https://sportbusy.com/world-cup-2026/teams/haiti/players/",
            "Haiti players",
            "Haiti players and squad details for the upcoming world cup tournament.",
        ),
        _item(
            "https://www.bbc.co.uk/sport/football/articles/haiti-world-cup-qualifying",
            "Haiti World Cup qualifying coverage",
            "BBC Sport coverage of Haiti's World Cup qualifying campaign and squad situation.",
        ),
        _item(
            "https://haitiantimes.com/2024/06/01/haiti-soccer-training-camp-for-2026-wcq/",
            "Haiti Men’s National Soccer Team struggles to gather players",
            "As of June 1, the Haiti Men’s National Soccer Team should have been in full swing preparing for the next stage of the FIFA World Cup qualifiers.",
        ),
    ]

    ranked = rerank_items(query, items, limit=3, domain="sports", intent="recent")
    urls = [item.url for item in ranked]

    assert "https://www.bbc.co.uk/sport/football/articles/haiti-world-cup-qualifying" in urls
    assert "https://haitiantimes.com/2024/06/01/haiti-soccer-training-camp-for-2026-wcq/" in urls
    assert not any("sportbusy.com" in url for url in urls)
    assert not any("fifa-worldcup26.com" in url for url in urls)


def test_rerank_drops_health_domains_for_soccer_lineup_query():
    query = "haiti national football team probable lineup"
    items = [
        _item(
            "https://www.cdc.gov/search/?query=haiti%20national%20football%20team%20probable%20lineup",
            "CDC Search",
            "Search results for haiti national football team probable lineup.",
        ),
        _item(
            "https://www.who.int/search?query=haiti%20national%20football%20team%20probable%20lineup",
            "WHO Search",
            "Search results page for haiti national football team probable lineup.",
        ),
        _item(
            "https://www.espn.com/soccer/team/_/id/123/haiti",
            "Haiti Team Profile",
            "ESPN soccer coverage for Haiti including squad and match information.",
        ),
        _item(
            "https://www.concacaf.com/en/world-cup-qualifying-men/game-details?matchid=123",
            "Concacaf Match Details",
            "Concacaf match details and lineup information for Haiti.",
        ),
    ]

    ranked = rerank_items(query, items, limit=4, domain="sports", intent="recent")
    urls = [item.url for item in ranked]

    assert any("espn.com/soccer" in url for url in urls)
    assert any("concacaf.com" in url for url in urls)
    assert not any("cdc.gov" in url for url in urls)
    assert not any("who.int" in url for url in urls)
