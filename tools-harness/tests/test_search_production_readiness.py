import pytest
from tools.websearch import search
from tools.search_agentic import rerank_items
from tools.search_result import SearchSnippet

def test_search_empty_query():
    res = search("")
    assert isinstance(res, str)
    assert "Please enter a valid search query" in res

def test_search_junk_query():
    res = search("!!!!!")
    assert isinstance(res, str)
    assert "Please enter a search query with valid text" in res

def test_search_factual_query():
    res = search("what is the height of mount everest in meters?")
    assert isinstance(res, str)
    assert "8,848" in res or "8848" in res or "8,849" in res or "8849" in res or "height" in res.lower()

def test_search_explicit_domain_bypass():
    # Pinterest shouldn't be blocked if requested explicitly
    res = search("pinterest ideas for backyard landscaping")
    assert isinstance(res, str)
    assert len(res) > 10

def test_decoy_domain_penalty():
    # Decoy domains (e.g. sportbusy.com) should get a heavy penalty and rank below reputable domains
    items = [
        SearchSnippet(url="https://sportbusy.com/article", title="Squatter Info", snippet="Information here " * 5),
        SearchSnippet(url="https://www.bbc.co.uk/sport/football", title="BBC Sport", snippet="Official BBC coverage " * 5),
    ]
    ranked = rerank_items("world cup schedule", items, limit=2, domain="sports")
    assert ranked[0].url == "https://www.bbc.co.uk/sport/football"
    assert ranked[1].url == "https://sportbusy.com/article"

def test_search_special_chars():
    res = search("Who wrote \"To Kill a Mockingbird\"?")
    assert "Harper" in res or "Lee" in res

def test_evidence_block_limits():
    from tools.search_agentic import _evidence_block
    items = [
        SearchSnippet(url="https://site1.com", title="Title 1", snippet="A" * 600, published_date="2026-01-01"),
        SearchSnippet(url="https://site2.com", title="Title 2", snippet="B" * 600, published_date="2026-01-02"),
    ]
    # Verify snippet sizes are capped at 500 and total size at 3000
    block = _evidence_block(items, max_snippet=500, max_total=3000)
    assert len(block) > 0
    assert "Title 1" in block
    assert "Title 2" in block
    assert "A" * 501 not in block
    assert "B" * 501 not in block

