"""
Live sports score lookup — bypasses snippet web search.

Snippet search (SearXNG/DDG/Tavily) returns cached, contradictory scorelines for
in-play matches. This hits SofaScore's keyless live-football endpoint directly:
one request returns every live match globally with real-time score + match state.

Public entry: lookup(query) -> str | None
  Returns a formatted live line, or None if no live match matches the query
  (caller falls back to normal web search).
"""

import difflib
import re
import subprocess
import json
import logging

log = logging.getLogger(__name__)

_LIVE_URL = "https://api.sofascore.com/api/v1/sport/football/events/live"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# Trigger only for score-intent queries naming two sides (vs / versus / against).
# Score/result keywords, either side of a vs/versus/against anchor. "result/final/who won"
# added so "who won France vs Sweden" and "France vs Sweden final" route to the live source
# instead of falling through to stale snippet search (the 0-0-then-3-0 inconsistency).
_SCORE_KW = (
    r"(?:score|scoreline|winning|won|result|results|final|"
    r"who'?s\s+up|how\s+(?:are|is)\b.*\bdoing)"
)
_LIVE_SCORE_RE = re.compile(
    rf"\b{_SCORE_KW}\b[\s\S]*\b(?:vs?\.?|versus|against)\b"
    rf"|\b(?:vs?\.?|versus|against)\b[\s\S]*\b{_SCORE_KW}\b",
    re.IGNORECASE,
)

# Filler stripped before matching team tokens. Team names are whatever's left.
_STOP = {
    "what", "whats", "what's", "is", "the", "live", "current", "currently", "now",
    "score", "scoreline", "of", "for", "in", "between", "and", "vs", "versus", "v",
    "against", "match", "game", "today", "tonight", "winning", "who", "whos", "who's",
    "result", "right", "this", "moment", "tell", "me", "at", "a", "an", "on", "doing",
    "how", "are", "they", "playing", "football", "soccer",
}


def is_live_score_query(query: str) -> bool:
    return bool(_LIVE_SCORE_RE.search(query))


def _team_tokens(query: str) -> list[str]:
    toks = re.findall(r"[a-zA-Z][a-zA-Z'.-]*", query.lower())
    return [t for t in toks if len(t) >= 3 and t not in _STOP]


def _fetch_live() -> list[dict]:
    # curl, not urllib: SofaScore's Cloudflare blocks Python's TLS fingerprint (403)
    # but lets curl through. ponytail: curl shell-out, swap for a TLS-spoofing lib if it breaks.
    out = subprocess.run(
        ["curl", "-s", "-m", "12", "-H", f"User-Agent: {_UA}", _LIVE_URL],
        capture_output=True, text=True, timeout=15,
    ).stdout
    return json.loads(out).get("events", [])


def _fuzzy_hit(token: str, words: list[str]) -> bool:
    """Substring match, or close enough to tolerate a common misspelling
    (e.g. "Columbia" vs "Colombia" — confirmed real query, 2026-07-07)."""
    return any(
        token in w or w in token or difflib.SequenceMatcher(None, token, w).ratio() >= 0.82
        for w in words
    )


def _match_event(events: list[dict], tokens: list[str]) -> dict | None:
    """Pick the live event whose team names contain (or closely match) the most query tokens (>=2)."""
    best, best_score = None, 0
    for e in events:
        names = f"{e['homeTeam']['name']} {e['awayTeam']['name']}".lower()
        words = names.split()
        hits = sum(1 for t in tokens if _fuzzy_hit(t, words))
        if hits > best_score:
            best, best_score = e, hits
    return best if best_score >= 2 else None


def _format(e: dict) -> str:
    h, a = e["homeTeam"]["name"], e["awayTeam"]["name"]
    hs = e.get("homeScore", {}).get("current", 0)
    as_ = e.get("awayScore", {}).get("current", 0)
    state = e.get("status", {}).get("description", "in progress")
    tour = e.get("tournament", {}).get("name", "")
    line = f"{h} {hs}–{as_} {a} — {state} (live)"
    if tour:
        line += f", {tour}"
    return line


def lookup(query: str) -> str | None:
    tokens = _team_tokens(query)
    if len(tokens) < 2:
        return None  # need both sides to disambiguate; let search handle it
    try:
        events = _fetch_live()
    except Exception as e:
        log.warning("[livescore] fetch failed: %s", e)
        return None
    e = _match_event(events, tokens)
    if not e:
        log.info("[livescore] no live match for tokens=%s (%d live games)", tokens, len(events))
        return None
    result = _format(e)
    log.info("[livescore] hit: %s", result)
    return result


def demo():
    events = [
        {"homeTeam": {"name": "France"}, "awayTeam": {"name": "Sweden"},
         "homeScore": {"current": 0}, "awayScore": {"current": 0},
         "status": {"description": "1st half"}, "tournament": {"name": "World Cup"}},
        {"homeTeam": {"name": "Academia U20"}, "awayTeam": {"name": "União U20"},
         "homeScore": {"current": 0}, "awayScore": {"current": 3},
         "status": {"description": "2nd half"}, "tournament": {"name": "Brazil U20"}},
    ]
    assert is_live_score_query("what is the live score of France vs Sweden now")
    assert is_live_score_query("who's winning France versus Sweden")
    assert not is_live_score_query("what's the weather in Paris")
    assert _team_tokens("live score of the France vs Sweden now") == ["france", "sweden"]
    e = _match_event(events, ["france", "sweden"])
    assert e and e["homeTeam"]["name"] == "France", e
    assert _match_event(events, ["barcelona", "madrid"]) is None
    assert _format(e) == "France 0–0 Sweden — 1st half (live), World Cup"
    print("livescore demo OK")


if __name__ == "__main__":
    demo()
