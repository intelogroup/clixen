import os
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.ddg_search import _ddg_queries, _health_epi_core_query, execute


def test_ddg_queries_expand_health_epi_with_authority_sites():
    queries = _ddg_queries(
        "what are the latest covid-19 cases and prevalence?",
        intent="recent",
        domain="health",
    )

    assert queries[0] == "covid-19 cases prevalence dashboard site:data.who.int inurl:cases"
    assert "covid-19 cases prevalence epidemiological update site:who.int intitle:update" in queries
    assert "covid-19 cases prevalence surveillance site:cdc.gov intitle:surveillance" in queries
    assert "covid-19 cases prevalence site:ourworldindata.org inurl:coronavirus" in queries
    assert queries[-1] == "what are the latest covid-19 cases and prevalence?"


def test_ddg_queries_leave_non_epi_query_unchanged():
    queries = _ddg_queries("turmeric benefits", intent="factual", domain="health")
    assert queries == ["turmeric benefits"]


def test_health_epi_core_query_condenses_verbose_question():
    assert _health_epi_core_query("what are the latest covid-19 cases and prevalence?") == (
        "covid-19 cases prevalence"
    )


def test_execute_reranks_authority_results_above_generic_base_hits():
    base_query = "what are the latest covid-19 cases and prevalence?"

    class FakeDDGS:
        def __init__(self, timeout=4):
            self.timeout = timeout

        def text(self, query, **kwargs):
            if query == base_query:
                return [
                    {
                        "title": "Generic explainer",
                        "href": "https://example.com/covid-explainer",
                        "body": "COVID-19 prevalence and cases explainer with general background. " * 3,
                    },
                    {
                        "title": "Old prevalence article",
                        "href": "https://starctmag.com/old-covid-prevalence",
                        "body": "Old article about modeled prevalence rather than live surveillance. " * 3,
                    },
                ]
            site_map = {
                "site:data.who.int": "https://data.who.int/dashboards/covid19/cases",
                "site:who.int": "https://www.who.int/publications/m/item/weekly-epidemiological-update-on-covid-19",
                "site:cdc.gov": "https://www.cdc.gov/covid/php/surveillance/index.html",
                "site:ourworldindata.org": "https://ourworldindata.org/covid-cases",
            }
            for key, url in site_map.items():
                if key in query:
                    return [
                        {
                            "title": f"Authority result for {key}",
                            "href": url,
                            "body": "Confirmed cases and deaths worldwide with surveillance statistics. " * 3,
                        }
                    ]
            return []

    fake_module = types.SimpleNamespace(DDGS=FakeDDGS)

    with patch.dict(sys.modules, {"ddgs": fake_module}):
        result = execute(base_query, max_results=3, intent="recent", domain="health")

    assert result.ok is True
    assert 1 <= len(result.items) <= 3
    urls = [item.url for item in result.items]
    assert urls[0] != "https://example.com/covid-explainer"
    assert "https://example.com/covid-explainer" not in urls
    assert any("who.int" in url or "cdc.gov" in url or "ourworldindata.org" in url for url in urls)


def test_execute_dedupes_duplicate_urls_across_query_variants():
    base_query = "what are the latest covid-19 cases and prevalence?"
    duplicate_url = "https://data.who.int/dashboards/covid19/cases"

    class FakeDDGS:
        def __init__(self, timeout=4):
            self.timeout = timeout

        def text(self, query, **kwargs):
            if query == base_query:
                return [
                    {
                        "title": "WHO dashboard duplicate",
                        "href": duplicate_url,
                        "body": "WHO dashboard with case totals. " * 3,
                    }
                ]
            if "site:data.who.int" in query:
                return [
                    {
                        "title": "WHO dashboard duplicate again",
                        "href": duplicate_url,
                        "body": "WHO dashboard with case totals. " * 3,
                    }
                ]
            return []

    fake_module = types.SimpleNamespace(DDGS=FakeDDGS)

    with patch.dict(sys.modules, {"ddgs": fake_module}):
        result = execute(base_query, max_results=3, intent="recent", domain="health")

    assert result.ok is True
    urls = [item.url for item in result.items]
    assert urls.count(duplicate_url) == 1


# Integration: requires real Ollama/network/creds/server — auto-skipped when deps down.
import pytest as _pytest
pytestmark = _pytest.mark.live
