import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from unittest.mock import patch

import pytest

from tools import livescore


_EVENTS = [
    {"homeTeam": {"name": "France"}, "awayTeam": {"name": "Sweden"},
     "homeScore": {"current": 0}, "awayScore": {"current": 0},
     "status": {"description": "1st half"}, "tournament": {"name": "World Cup"}},
    {"homeTeam": {"name": "Academia U20"}, "awayTeam": {"name": "União U20"},
     "homeScore": {"current": 0}, "awayScore": {"current": 3},
     "status": {"description": "2nd half"}, "tournament": {"name": "Brazil U20"}},
    {"homeTeam": {"name": "Manchester United"}, "awayTeam": {"name": "Liverpool"},
     "homeScore": {"current": 2}, "awayScore": {"current": 1},
     "status": {"description": "2nd half"}, "tournament": {"name": "Premier League"}},
]


@pytest.mark.parametrize("q", [
    "what is the live score of France vs Sweden now",
    "who's winning France versus Sweden",
    "score France vs Sweden",
    "current scoreline Man United against Liverpool",
])
def test_is_live_score_query_true(q):
    assert livescore.is_live_score_query(q)


# Widened result/final/won phrasings — used to fall through to stale snippet search.
@pytest.mark.parametrize("q", [
    "who won France vs Sweden",
    "France vs Sweden final",
    "France vs Sweden final score",
    "what was the result of Man United vs Liverpool",
    "France versus Sweden result",
])
def test_is_live_score_query_result_phrasings(q):
    assert livescore.is_live_score_query(q)


@pytest.mark.parametrize("q", [
    "what's the weather in Paris",
    "who is the president of France",
    "tell me about France",            # no score + no vs
    "France history",
    "the final chapter of the book",   # 'final' without a vs anchor must not trigger
])
def test_is_live_score_query_false(q):
    assert not livescore.is_live_score_query(q)


def test_team_tokens_strips_filler():
    assert livescore._team_tokens("live score of the France vs Sweden now") == ["france", "sweden"]
    assert livescore._team_tokens("Man United vs Liverpool") == ["man", "united", "liverpool"]


def test_match_event_picks_both_teams():
    e = livescore._match_event(_EVENTS, ["france", "sweden"])
    assert e and e["homeTeam"]["name"] == "France"


def test_match_event_multiword_team():
    e = livescore._match_event(_EVENTS, ["manchester", "united", "liverpool"])
    assert e and e["awayTeam"]["name"] == "Liverpool"


def test_match_event_no_match_returns_none():
    assert livescore._match_event(_EVENTS, ["barcelona", "madrid"]) is None


def test_match_event_single_token_below_threshold():
    # one hit only ("france") — must not match on a single common token
    assert livescore._match_event(_EVENTS, ["france"]) is None


def test_format():
    assert livescore._format(_EVENTS[0]) == "France 0–0 Sweden — 1st half (live), World Cup"


def test_lookup_needs_two_tokens():
    # single team → defer to web search, never even fetches
    with patch.object(livescore, "_fetch_live") as f:
        assert livescore.lookup("what's the France score") is None
        f.assert_not_called()


def test_lookup_hit():
    with patch.object(livescore, "_fetch_live", return_value=_EVENTS):
        assert livescore.lookup("live score France vs Sweden") == \
            "France 0–0 Sweden — 1st half (live), World Cup"


def test_lookup_no_live_match_falls_through():
    with patch.object(livescore, "_fetch_live", return_value=_EVENTS):
        assert livescore.lookup("live score Barcelona vs Madrid") is None


def test_lookup_fetch_failure_returns_none():
    with patch.object(livescore, "_fetch_live", side_effect=RuntimeError("403")):
        assert livescore.lookup("live score France vs Sweden") is None


@pytest.mark.live
def test_lookup_real_endpoint_shape():
    # Only asserts the pipeline runs and returns str|None — score content is non-deterministic.
    out = livescore.lookup("live score France vs Sweden")
    assert out is None or isinstance(out, str)
