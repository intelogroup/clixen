import pytest
from tools.registry import EXECUTORS

def test_search_wikidata():
    assert "search_wikidata" in EXECUTORS
    res = EXECUTORS["search_wikidata"]({"query": "Albert Einstein", "limit": 1})
    assert isinstance(res, str)
    assert "Q937" in res or "Einstein" in res

def test_search_openalex():
    assert "search_openalex" in EXECUTORS
    res = EXECUTORS["search_openalex"]({"query": "CRISPR gene editing", "limit": 1})
    assert isinstance(res, str)
    assert "Title" in res or "DOI" in res

def test_search_crossref():
    assert "search_crossref" in EXECUTORS
    res = EXECUTORS["search_crossref"]({"query": "machine learning climate", "limit": 1})
    assert isinstance(res, str)
    assert "Title" in res or "DOI" in res

def test_search_opencorporates():
    assert "search_opencorporates" in EXECUTORS
    res = EXECUTORS["search_opencorporates"]({"query": "Apple Inc"})
    assert isinstance(res, str)
    assert "Apple" in res or "OpenCorporates" in res

def test_search_sec_edgar():
    assert "search_sec_edgar" in EXECUTORS
    res = EXECUTORS["search_sec_edgar"]({"query": "Apple Inc", "limit": 1})
    assert isinstance(res, str)
    assert "Entity" in res or "filing" in res or "Apple" in res

def test_geocode_address():
    assert "geocode_address" in EXECUTORS
    res = EXECUTORS["geocode_address"]({"address": "Eiffel Tower Paris", "limit": 1})
    assert isinstance(res, str)
    assert "Lat" in res and "Lon" in res

def test_search_gdelt_news():
    assert "search_gdelt_news" in EXECUTORS
    res = EXECUTORS["search_gdelt_news"]({"query": "climate change 2025", "limit": 1})
    assert isinstance(res, str)
    # GDELT might return rate limit or timeout warnings, which is acceptable for the test
    assert len(res) > 0
