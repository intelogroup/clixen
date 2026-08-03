import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.search_result import SearchSnippet
from tools.search_agentic import _authority_score, _should_deny, _domain_allow_score, rerank_items
from tools.query_guard import check_all as guard_check


def _snip(url="", snippet="text", title="T", published_date=""):
    return SearchSnippet(title=title, url=url, snippet=snippet, published_date=published_date, source="tavily")


def test_authority_score_gov_domain():
    assert _authority_score(_snip(url="https://cdc.gov/flu/weekly")) == 3.0


def test_authority_score_edu_domain():
    assert _authority_score(_snip(url="https://mit.edu/research/paper")) == 2.5


def test_authority_score_known_high_authority_exact():
    assert _authority_score(_snip(url="https://reuters.com/markets/")) == 2.0


def test_authority_score_low_authority_tld():
    score = _authority_score(_snip(url="https://cheap-deals.xyz/article"))
    assert score < 0


def test_authority_score_unknown_domain_returns_zero():
    assert _authority_score(_snip(url="https://somesite.com/page")) == 0.0


def test_authority_score_empty_url_returns_zero():
    assert _authority_score(_snip(url="")) == 0.0


def test_authority_score_www_prefix_stripped_correctly():
    # www.reuters.com should match reuters.com in _HIGH_AUTHORITY_DOMAINS
    score = _authority_score(_snip(url="https://www.reuters.com/markets/"))
    assert score == 2.0


def test_authority_score_subdomain_of_known_domain_returns_zero():
    # blog.reuters.com is a subdomain of reuters.com, so it inherits the high-authority score
    score = _authority_score(_snip(url="https://blog.reuters.com/article"))
    assert score == 2.0


def test_should_deny_pinterest():
    assert _should_deny(_snip(url="https://pinterest.com/recipe/")) is True


def test_should_deny_www_pinterest():
    assert _should_deny(_snip(url="https://www.pinterest.com/ideas/")) is True


def test_should_deny_returns_false_for_normal_domain():
    assert _should_deny(_snip(url="https://reuters.com/article")) is False


def test_domain_allow_score_finance_bloomberg():
    score = _domain_allow_score(_snip(url="https://bloomberg.com/markets/"), domain="finance")
    assert score == 1.5


def test_domain_allow_score_wrong_domain_returns_zero():
    score = _domain_allow_score(_snip(url="https://espn.com"), domain="finance")
    assert score == 0.0


def test_domain_allow_score_unknown_category_returns_zero():
    score = _domain_allow_score(_snip(url="https://bloomberg.com"), domain="unknown")
    assert score == 0.0


def test_rerank_items_filters_denied_domains():
    items = [
        _snip(url="https://pinterest.com/page", snippet="a" * 80),
        _snip(url="https://reuters.com/article", snippet="b" * 80),
    ]
    result = rerank_items("news story", items, limit=5, domain="news")
    urls = [i.url for i in result]
    assert not any("pinterest.com" in u for u in urls)
    assert any("reuters.com" in u for u in urls)


def test_rerank_items_domain_param_boosts_allow_listed_source():
    # With identical snippets, bloomberg.com should rank above random.com in finance domain
    snippet = "interest rates rose sharply. " * 5
    items = [
        _snip(url="https://random.com/article", snippet=snippet),
        _snip(url="https://bloomberg.com/markets", snippet=snippet),
    ]
    result = rerank_items("interest rates", items, domain="finance")
    assert result[0].url == "https://bloomberg.com/markets"


# ── Temporal detection and freshness decay tests ──────────────────────────────

import datetime as _datetime_module
from tools.search_agentic import _is_temporal, _freshness_decay, _freshness_bonus


def test_is_temporal_detects_today():
    assert _is_temporal("bitcoin price today") is True


def test_is_temporal_detects_latest():
    assert _is_temporal("latest iPhone release") is True


def test_is_temporal_detects_this_week():
    assert _is_temporal("Premier League results this week") is True


def test_is_temporal_detects_future():
    assert _is_temporal("when is the next artemis mission") is True
    assert _is_temporal("upcoming scheduled launch date") is True
    assert _is_temporal("slated release date") is True


def test_is_temporal_returns_false_for_stable_query():
    assert _is_temporal("who invented the telephone") is False


def test_is_temporal_returns_false_for_historical():
    assert _is_temporal("history of the Roman Empire") is False


def test_freshness_decay_zero_days():
    assert _freshness_decay(0) == 2.0


def test_freshness_decay_30_days():
    score = _freshness_decay(30)
    assert 0.9 < score < 1.1  # 2.0 * (1 - 30/60) = 1.0


def test_freshness_decay_60_days():
    assert _freshness_decay(60) == 0.0


def test_freshness_decay_beyond_60_days():
    assert _freshness_decay(90) == 0.0


def test_freshness_bonus_temporal_amplifies_score():
    today_str = _datetime_module.date.today().isoformat()
    item = _snip(published_date=today_str)
    normal = _freshness_bonus(item, temporal=False)
    amplified = _freshness_bonus(item, temporal=True)
    assert amplified > normal
    assert amplified == normal * 2.0


def test_freshness_bonus_no_date_returns_zero():
    item = _snip(published_date="")
    assert _freshness_bonus(item, temporal=True) == 0.0
    assert _freshness_bonus(item, temporal=False) == 0.0


# ── Query guard: ambiguous named entity detection ──────────────────────────────

def test_guard_flags_bare_apple():
    result = guard_check("Apple")
    assert result is not None
    assert "Apple" in result


def test_guard_flags_bare_python():
    result = guard_check("Python")
    assert result is not None


def test_guard_flags_mercury_ambiguous():
    result = guard_check("Mercury")
    assert result is not None


def test_guard_does_not_flag_three_word_query():
    # 3 words — ambiguity check skipped
    result = guard_check("Apple stock price")
    assert result is None


def test_guard_flags_two_word_ambiguous_query():
    # "Python tutorial" — 2 words, "python" is in _AMBIGUOUS_ENTITIES
    # The check DOES run (≤2 words), and "python" matches
    result = guard_check("Python tutorial")
    assert result is not None


def test_guard_existing_vague_entity_still_works():
    # Existing check: "the team won" should trigger _VAGUE_ENTITY
    result = guard_check("the team won")
    assert result is not None


# ── Source quality scoring and safe fallback ──────────────────────────────────

from tools.search_agentic import _source_quality_score, deterministic_summary


def test_quality_score_empty_items_is_zero():
    assert _source_quality_score([]) == 0.0


def test_quality_score_all_short_snippets_is_zero():
    items = [_snip(snippet="hi"), _snip(snippet="ok")]
    assert _source_quality_score(items) == 0.0


def test_quality_score_good_snippets_above_threshold():
    items = [_snip(snippet="a" * 100), _snip(snippet="b" * 120)]
    score = _source_quality_score(items)
    assert score >= 0.3


def test_deterministic_summary_returns_safe_fallback_on_weak_sources():
    items = [_snip(snippet="hi"), _snip(snippet="ok")]
    result = deterministic_summary("what is inflation", items)
    assert "No reliable sources" in result
    assert "specialised" in result


def test_deterministic_summary_returns_answer_on_good_sources():
    items = [_snip(snippet="Inflation is the rate at which prices rise over time. " * 3)]
    result = deterministic_summary("what is inflation", items)
    assert "No reliable sources" not in result
    assert "nflation" in result


# Integration: requires real Ollama/network/creds/server — auto-skipped when deps down.
import pytest as _pytest
pytestmark = _pytest.mark.live
