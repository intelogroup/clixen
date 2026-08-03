"""
Self-contained Sofascore connector. Primary path: RapidAPI SportAPI subscription
at sportapi7.p.rapidapi.com (reliable, ~192ms, no WAF issues).
Fallback: direct API at api.sofascore.com with curl_cffi.

Tools: search teams, get team info, live scores, scheduled events, match details,
standings, tournaments, team fixtures.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date as date_type
from pathlib import Path

log = logging.getLogger("connector_sofascore")
_DATA_DIR = Path.home() / ".config" / "g4l" / "data"

# curl_cffi for direct API fallback (TLS impersonation bypasses Sofascore WAF)
try:
    from curl_cffi import requests as curl_requests
    _HAS_CURL = True
except ImportError:
    curl_requests = None
    _HAS_CURL = False

# RapidAPI SportAPI subscription (primary)
_RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
_RAPIDAPI_HOST = "sportapi7.p.rapidapi.com"

_DIRECT_BASE = "https://api.sofascore.com/api/v1"
_RAPIDAPI_BASE = f"https://{_RAPIDAPI_HOST}/api/v1"

_HEADERS_DIRECT = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Accept": "application/json",
}
_HEADERS_RAPIDAPI = {
    "X-RapidAPI-Key": _RAPIDAPI_KEY,
    "X-RapidAPI-Host": _RAPIDAPI_HOST,
}

SPORT_IDS = {
    "football": 1, "soccer": 1,
    "basketball": 3,
    "tennis": 5,
    "american-football": 16, "nfl": 16,
    "baseball": 19, "mlb": 19,
    "hockey": 4, "nhl": 4,
    "rugby": 7,
    "cricket": 22,
    "handball": 6,
    "volleyball": 23,
    "boxing": 9,
    "mma": 11,
    "esports": 29,
}


def _save(data: dict, prefix: str = "sofascore") -> str:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _DATA_DIR / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    p.write_text(json.dumps(data, indent=2, default=str))
    return str(p)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(endpoint: str, use_rapidapi: bool = True, params: dict | None = None) -> dict:
    """GET from Sofascore API via RapidAPI (primary) or direct (fallback)."""
    if use_rapidapi:
        import httpx
        url = f"{_RAPIDAPI_BASE}{endpoint}"
        try:
            resp = httpx.get(url, headers=_HEADERS_RAPIDAPI, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            status = getattr(e, 'response', None) and getattr(e.response, 'status_code', None)
            detail = ""
            if status:
                try:
                    detail = e.response.text[:300]
                except Exception:
                    pass
            return {"error": f"RapidAPI HTTP {status}: {detail}" if status else f"RapidAPI: {str(e)[:300]}", "status": status or 0}

    url = f"{_DIRECT_BASE}{endpoint}"
    try:
        if _HAS_CURL:
            resp = curl_requests.get(url, headers=_HEADERS_DIRECT, params=params, timeout=15, impersonate="chrome120")
        else:
            import httpx
            resp = httpx.get(url, headers=_HEADERS_DIRECT, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        status = getattr(e, 'response', None) and getattr(e.response, 'status_code', None)
        detail = ""
        if status:
            try:
                detail = e.response.text[:300]
            except Exception:
                pass
        return {"error": f"Direct HTTP {status}: {detail}" if status else f"Direct: {str(e)[:300]}", "status": status or 0}


def _get_both(endpoint: str, params: dict | None = None) -> dict:
    """Try RapidAPI first, fall back to direct API."""
    result = _get(endpoint, use_rapidapi=True, params=params)
    if "error" not in result:
        return result
    log.warning("RapidAPI failed, trying direct API: %s", result.get("error"))
    result2 = _get(endpoint, use_rapidapi=False, params=params)
    if "error" not in result2:
        return result2
    # Return the RapidAPI error (it's more likely to work on retry)
    return result


def _fmt_date(d: date_type) -> str:
    return d.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Helper: resolve sport name to ID
# ---------------------------------------------------------------------------

def _resolve_sport(sport: str) -> tuple[str, int]:
    """Return (slug, id) for a sport name."""
    s = sport.lower().strip()
    if s in SPORT_IDS:
        for slug, sid in SPORT_IDS.items():
            if slug == s:
                return slug, sid
    if s.isdigit():
        return "football", int(s)
    return "football", 1


# ---------------------------------------------------------------------------
# Search teams
# ---------------------------------------------------------------------------

def search_teams(query: str, sport: str = "football") -> str:
    """Search for teams by name across all competitions."""
    data = _get_both(f"/search/teams/{query}")
    if "error" in data:
        return f"Error: {data['error']}"
    teams = data.get("teams", data.get("results", []))
    if not teams:
        return f"No teams found for '{query}'."
    lines = [f"Team search results for '{query}':", ""]
    for t in teams[:20]:
        name = t.get("name", "?")
        slug = t.get("slug", "")
        tid = t.get("id", "?")
        sport_name = t.get("sport", {}).get("name", "")
        cc = t.get("country", {}).get("name", "")
        lines.append(f"  [{tid}] {name} ({sport_name}, {cc})  https://www.sofascore.com/team/{slug}/{tid}")
    saved = _save({"query": query, "teams": teams}, "sofascore_teams")
    lines.append(f"\nFull data: {saved}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Get team info
# ---------------------------------------------------------------------------

def get_team(team_id: int) -> str:
    """Get team details, last matches, and upcoming fixtures."""
    info = _get_both(f"/team/{team_id}")
    if "error" in info:
        return f"Error fetching team: {info['error']}"
    team = info.get("team", info)

    # Last matches
    last = _get_both(f"/team/{team_id}/events/last/10")
    last_events = last.get("events", []) if "error" not in last else []

    # Upcoming
    upcoming = _get_both(f"/team/{team_id}/events/next/10")
    next_events = upcoming.get("events", []) if "error" not in upcoming else []

    name = team.get("name", "?")
    sport = team.get("sport", {}).get("name", "?")
    country = team.get("country", {}).get("name", "")
    cc = team.get("country", {}).get("alpha2", "")
    slug = team.get("slug", "")

    lines = [
        f"{name} ({country}) [{cc}]",
        f"Sport: {sport}",
        f"Sofascore: https://www.sofascore.com/team/{slug}/{team_id}",
        "",
    ]

    if last_events:
        lines.append(f"Last {len(last_events)} matches:")
        for e in last_events[:5]:
            home = e.get("homeTeam", {}).get("name", "?")
            away = e.get("awayTeam", {}).get("name", "?")
            hs = e.get("homeScore", {}).get("display", "?")
            as_ = e.get("awayScore", {}).get("display", "?")
            comp = e.get("tournament", {}).get("name", "?")
            ts = e.get("startTimestamp", 0)
            d = time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "?"
            lines.append(f"  {d} | {home} {hs}-{as_} {away} ({comp})")
        lines.append("")

    if next_events:
        lines.append(f"Next {len(next_events)} fixtures:")
        for e in next_events[:5]:
            home = e.get("homeTeam", {}).get("name", "?")
            away = e.get("awayTeam", {}).get("name", "?")
            comp = e.get("tournament", {}).get("name", "?")
            ts = e.get("startTimestamp", 0)
            d = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)) if ts else "?"
            lines.append(f"  {d} | {home} vs {away} ({comp})")

    saved = _save({"team": team, "last_events": last_events, "next_events": next_events}, f"sofascore_team_{team_id}")
    lines.append(f"\nFull data: {saved}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scheduled events for a date
# ---------------------------------------------------------------------------

def get_scheduled_events(sport: str = "football", date_str: str | None = None) -> str:
    """Get all scheduled events for a given sport and date."""
    d = date_str or _fmt_date(date_type.today())
    sport_slug, _ = _resolve_sport(sport)
    data = _get_both(f"/sport/{sport_slug}/scheduled-events/{d}")
    if "error" in data:
        return f"Error: {data['error']}"
    events = data.get("events", [])
    if not events:
        return f"No {sport} events scheduled for {d}."
    lines = [f"{sport.title()} events on {d} ({len(events)} matches):", ""]
    for e in events[:30]:
        home = e.get("homeTeam", {}).get("name", "?")
        away = e.get("awayTeam", {}).get("name", "?")
        comp = e.get("tournament", {}).get("name", "?")
        status = e.get("status", {}).get("description", "")
        hs = e.get("homeScore", {}).get("display", "")
        as_ = e.get("awayScore", {}).get("display", "")
        ts = e.get("startTimestamp", 0)
        t = time.strftime("%H:%M", time.gmtime(ts)) if ts else "?"
        eid = e.get("id", "?")
        score = f" {hs}-{as_}" if hs else ""
        lines.append(f"  [{eid}] {t} {home}{score} vs {away}  [{comp}] {status}")
    saved = _save({"date": d, "sport": sport_slug, "events": events}, "sofascore_scheduled")
    lines.append(f"\nFull data: {saved}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Live scores
# ---------------------------------------------------------------------------

def get_live_scores(sport: str = "football") -> str:
    """Get all currently live events for a sport."""
    sport_slug, _ = _resolve_sport(sport)
    data = _get_both(f"/sport/{sport_slug}/events/live")
    if "error" in data:
        return f"Error: {data['error']}"
    events = data.get("events", [])
    if not events:
        return f"No live {sport} events right now."
    lines = [f"Live {sport.title()} ({len(events)} matches):", ""]
    for e in events[:30]:
        home = e.get("homeTeam", {}).get("name", "?")
        away = e.get("awayTeam", {}).get("name", "?")
        hs = e.get("homeScore", {}).get("display", "0")
        as_ = e.get("awayScore", {}).get("display", "0")
        comp = e.get("tournament", {}).get("name", "?")
        status = e.get("status", {}).get("description", "")
        minute = e.get("time", {}).get("currentPeriodStart", "")
        eid = e.get("id", "?")
        tm = f" {minute}'" if minute else ""
        lines.append(f"  [{eid}] {home} {hs}-{as_} {away}  [{comp}] {status}{tm}")
    saved = _save({"sport": sport_slug, "events": events}, "sofascore_live")
    lines.append(f"\nFull data: {saved}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Match / event details
# ---------------------------------------------------------------------------

def get_event(event_id: int) -> str:
    """Get full match details: score, teams, incidents, lineups, stats."""
    info = _get_both(f"/event/{event_id}")
    if "error" in info:
        return f"Error: {info['error']}"
    e = info.get("event", info)

    incidents_data = _get_both(f"/event/{event_id}/incidents")
    incidents = incidents_data.get("incidents", []) if "error" not in incidents_data else []

    lineups_data = _get_both(f"/event/{event_id}/lineups")
    lineups = lineups_data if "error" not in lineups_data else {}

    stats_data = _get_both(f"/event/{event_id}/statistics")
    stats = stats_data.get("statistics", []) if "error" not in stats_data else []

    home = e.get("homeTeam", {}).get("name", "?")
    away = e.get("awayTeam", {}).get("name", "?")
    hs = e.get("homeScore", {}).get("display", "?")
    as_ = e.get("awayScore", {}).get("display", "?")
    comp = e.get("tournament", {}).get("name", "?")
    status = e.get("status", {}).get("description", "")
    ts = e.get("startTimestamp", 0)
    d = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)) if ts else "?"
    venue = e.get("venue", {})
    stadium = venue.get("stadium", {}).get("name", "?") if venue else "?"
    city = venue.get("city", {}).get("name", "?") if venue else "?"

    lines = [
        f"{home} {hs} - {as_} {away}",
        f"Competition: {comp} | Status: {status}",
        f"Date: {d} | Venue: {stadium}, {city}",
        f"Sofascore: https://www.sofascore.com/match/{e.get('slug', '')}/{event_id}",
        "",
    ]

    if incidents:
        lines.append("Key incidents:")
        for inc in incidents[:15]:
            pname = inc.get("playerName", inc.get("player", {}).get("name", "?"))
            inc_type = inc.get("incidentType", "?")
            inc_class = inc.get("incidentClass", "")
            minute = inc.get("time", "?")
            icon = {"goal": "G", "card": "", "substitution": "SUB", "goalown": "OG",
                     "penalty": "PEN", "missedpenalty": "MP"}.get(inc_class, inc_class[:3].upper())
            side = "H" if inc.get("isHome") else "A"
            g = f" {inc.get('homeScore', '')}-{inc.get('awayScore', '')}" if inc.get("homeScore") is not None else ""
            lines.append(f"  [{minute}'] {icon} {side} {pname}{g} ({inc_type})")
        lines.append("")

    if lineups:
        for side_key, side_label in [("home", "Home"), ("away", "Away")]:
            formation = lineups.get(side_key, {}).get("formation", "?")
            players = lineups.get(side_key, {}).get("players", [])
            if players:
                lines.append(f"{side_label} lineup (4-{formation}):")
                for p in players[:14]:
                    pn = p.get("player", {}).get("name", "?")
                    pos = p.get("player", {}).get("position", "")
                    sub = " (sub)" if p.get("substitute") else ""
                    lines.append(f"  {pn} [{pos}]{sub}")
                lines.append("")

    if stats:
        lines.append("Match statistics:")
        for cat in stats[:8]:
            if isinstance(cat, dict):
                name = cat.get("name", "?")
                groups = cat.get("groups", [])
                for g in groups[:5]:
                    items = g.get("statisticsItems", [])
                    for s_item in items[:5]:
                        hn = s_item.get("home", "")
                        an = s_item.get("away", "")
                        sn = s_item.get("name", "")
                        lines.append(f"  {sn}: {hn} vs {an}")
        lines.append("")

    saved = _save({
        "event": e, "incidents": incidents, "lineups": lineups, "statistics": stats,
    }, f"sofascore_event_{event_id}")
    lines.append(f"Full data: {saved}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standings / league table
# ---------------------------------------------------------------------------

def get_standings(tournament_id: int, season_id: int | None = None) -> str:
    """Get league standings for a tournament. If no season_id, fetches latest season first."""
    if not season_id:
        seasons_data = _get_both(f"/unique-tournament/{tournament_id}/seasons")
        seasons = seasons_data.get("seasons", []) if "error" not in seasons_data else []
        if not seasons:
            return f"No seasons found for tournament {tournament_id}."
        season_id = seasons[-1]["id"]

    data = _get_both(f"/unique-tournament/{tournament_id}/season/{season_id}/standings/total")
    if "error" in data:
        return f"Error: {data['error']}"
    standings_list = data.get("standings", [])
    if not standings_list:
        return f"No standings for tournament {tournament_id}, season {season_id}."
    s = standings_list[0]
    rows = s.get("rows", [])
    name = s.get("name", f"Tournament {tournament_id}")
    lines = [f"{name} — Standings:", ""]
    lines.append(f"{'#':>2s} | {'Team':<30s} | {'P':>2s} | {'W':>2s} | {'D':>2s} | {'L':>2s} | {'+/-':>5s} | {'Pts':>3s}")
    lines.append("-" * 65)
    for row in rows[:24]:
        pos = row.get("position", "?")
        tname = row.get("team", {}).get("name", "?")
        matches = row.get("matches", "?")
        wins = row.get("wins", 0)
        draws = row.get("draws", 0)
        losses = row.get("losses", 0)
        gf = row.get("scoresFor", 0)
        ga = row.get("scoresAgainst", 0)
        gd = gf - ga
        pts = row.get("points", 0)
        lines.append(f"{pos:>2d} | {tname:<30s} | {matches:>2d} | {wins:>2d} | {draws:>2d} | {losses:>2d} | {gd:+d} | {pts:>3d}")
    saved = _save({"tournament_id": tournament_id, "season_id": season_id, "standings": data}, "sofascore_standings")
    lines.append(f"\nFull data: {saved}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------

def get_tournaments(sport: str = "football") -> str:
    """List top tournaments for a sport from Sofascore's popular entities."""
    sport_slug, _ = _resolve_sport(sport)
    data = _get_both(f"/config/popular-entities/US")
    if "error" in data:
        return f"Error: {data['error']}"
    sport_key = {"football": "football", "basketball": "basketball", "tennis": "tennis",
                 "american-football": "american-football", "baseball": "baseball",
                 "ice-hockey": "ice-hockey", "mma": "mma", "esports": "esports"}.get(sport_slug, sport_slug)
    sport_data = data.get(sport_key, {})
    tournaments = sport_data.get("uniqueTournaments", [])
    if not tournaments:
        return f"No tournaments found for {sport}."
    lines = [f"Top {sport.title()} tournaments ({len(tournaments)}):", ""]
    for t in tournaments[:30]:
        name = t.get("name", "?")
        tid = t.get("id", "?")
        cat = t.get("category", {}).get("name", "")
        lines.append(f"  [{tid}] {name} ({cat})")
    saved = _save({"sport": sport_slug, "tournaments": tournaments}, "sofascore_tournaments")
    lines.append(f"\nFull data: {saved}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SOFASCORE_SEARCH_TEAMS_SCHEMA = {
    "type": "function", "function": {
        "name": "sofascore_search_teams",
        "description": "Search for sports teams by name on Sofascore. Returns team ID, name, sport, country. Use the team ID with other sofascore functions.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Team name to search for (e.g. 'Liverpool', 'Barcelona', 'Celtics')"},
            "sport": {"type": "string", "description": "Sport name (e.g. football, basketball, tennis)", "default": "football"},
        }, "required": ["query"]},
    },
}

SOFASCORE_GET_TEAM_SCHEMA = {
    "type": "function", "function": {
        "name": "sofascore_get_team",
        "description": "Get team details, last 5 results, and next 5 fixtures from Sofascore.",
        "parameters": {"type": "object", "properties": {
            "team_id": {"type": "integer", "description": "Sofascore team ID (search with sofascore_search_teams first)"},
        }, "required": ["team_id"]},
    },
}

SOFASCORE_SCHEDULED_EVENTS_SCHEMA = {
    "type": "function", "function": {
        "name": "sofascore_scheduled_events",
        "description": "Get all scheduled/finished events for a sport on a given date. Today by default.",
        "parameters": {"type": "object", "properties": {
            "sport": {"type": "string", "description": "Sport name (football, basketball, tennis, etc.)", "default": "football"},
            "date": {"type": "string", "description": "Date in YYYY-MM-DD format. Defaults to today."},
        }, "required": []},
    },
}

SOFASCORE_LIVE_SCORES_SCHEMA = {
    "type": "function", "function": {
        "name": "sofascore_live_scores",
        "description": "Get all currently live events for a sport from Sofascore. Shows score, minute, competition.",
        "parameters": {"type": "object", "properties": {
            "sport": {"type": "string", "description": "Sport name (football, basketball, tennis, etc.)", "default": "football"},
        }, "required": []},
    },
}

SOFASCORE_GET_EVENT_SCHEMA = {
    "type": "function", "function": {
        "name": "sofascore_get_event",
        "description": "Get full match details: score, teams, incidents (goals/cards), lineups, match statistics.",
        "parameters": {"type": "object", "properties": {
            "event_id": {"type": "integer", "description": "Sofascore event/match ID"},
        }, "required": ["event_id"]},
    },
}

SOFASCORE_GET_STANDINGS_SCHEMA = {
    "type": "function", "function": {
        "name": "sofascore_get_standings",
        "description": "Get league table standings for a tournament. Automatically fetches the latest season.",
        "parameters": {"type": "object", "properties": {
            "tournament_id": {"type": "integer", "description": "Sofascore tournament ID (from sofascore_tournaments)"},
            "season_id": {"type": "integer", "description": "Optional season ID. Auto-detects latest if omitted."},
        }, "required": ["tournament_id"]},
    },
}

SOFASCORE_GET_TOURNAMENTS_SCHEMA = {
    "type": "function", "function": {
        "name": "sofascore_get_tournaments",
        "description": "List top tournaments/leagues for a sport with follower counts.",
        "parameters": {"type": "object", "properties": {
            "sport": {"type": "string", "description": "Sport name (football, basketball, tennis, etc.)", "default": "football"},
        }, "required": []},
    },
}

SCHEMAS = [
    SOFASCORE_SEARCH_TEAMS_SCHEMA,
    SOFASCORE_GET_TEAM_SCHEMA,
    SOFASCORE_SCHEDULED_EVENTS_SCHEMA,
    SOFASCORE_LIVE_SCORES_SCHEMA,
    SOFASCORE_GET_EVENT_SCHEMA,
    SOFASCORE_GET_STANDINGS_SCHEMA,
    SOFASCORE_GET_TOURNAMENTS_SCHEMA,
]
