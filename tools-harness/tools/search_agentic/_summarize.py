"""Agentic post-search pipeline — evidence formatting, model summarization, deterministic fallback."""

from __future__ import annotations

import datetime as _dt
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

_log = logging.getLogger(__name__)

from tools.search_result import SearchResult, SearchSnippet
from clients.cancellation import check_aborted

from ._scoring import (
    _CONTENT_YEAR_RE, _ENTITY_STOPWORDS, _JS_MARKERS, _LARGE_NUMERIC_CLAIM_RE, _QUALITY_THRESHOLD,
    _authority_score, _clean, _coerce_item, _domain_allow_score, _freshness_decay, _is_nav_noise,
    _is_temporal, _matches_domain_or_subdomain, _parse_date, _query_overlap_score, _safe_shutdown,
    _should_deny, _source_quality_score, _tokenize,
)

def _extract_query_entity_tokens(query: str) -> frozenset[str]:
    """Return the meaningful entity tokens from a query (stopwords removed, len >= 3).

    Uses >= 3 (not > 3) so short but highly specific medical/disease tokens like
    'pox', 'flu', 'hiv', 'rna' are included rather than silently dropped.

    Strips site: operator tokens (e.g. site:cdc.gov) before tokenizing so that
    rewritten queries with site constraints don't pollute entity matching.
    """
    # Remove site: operator fragments before tokenizing — they are search operators,
    # not content tokens, and must not participate in entity lock matching.
    clean_query = re.sub(r"site:\S+", "", query, flags=re.IGNORECASE).strip()
    tokens = _tokenize(clean_query.lower())
    return frozenset(t for t in tokens if len(t) >= 3 and t not in _ENTITY_STOPWORDS)


def _entity_lock_penalty(query: str, item: SearchSnippet) -> float:
    """Penalise items whose content does not mention the query's key entity tokens.

    Prevents a hot unrelated story (e.g. measles outbreak) from outranking a
    quieter but correct topic (e.g. varicella/chickenpox) purely on freshness.
    """
    # Known disease aliases — expand before matching so "chickenpox" query
    # matches items that use "varicella" and vice versa.
    _ALIAS_MAP = {
        "chickenpox": {"varicella"},
        "varicella": {"chickenpox"},
        "covid": {"covid-19", "sars-cov-2", "coronavirus"},
        "covid-19": {"covid", "sars-cov-2", "coronavirus"},
        "flu": {"influenza", "avian influenza", "bird flu"},
        "influenza": {"flu", "bird flu", "avian influenza"},
        "bird": {"avian", "poultry"},
        "hpai": {"h5n1", "h5n2", "avian influenza", "bird flu"},
        "h5n1": {"hpai", "avian influenza", "bird flu"},
        "mpox": {"monkeypox"},
        "monkeypox": {"mpox"},
        "ebola": {"ebola hemorrhagic fever"},
        "dengue": {"dengue fever", "breakbone fever"},
    }

    entity_tokens = _extract_query_entity_tokens(query)
    if not entity_tokens:
        return 0.0
    text = f"{(item.title or '').lower()} {(item.snippet or '').lower()}"
    matched = 0
    for t in entity_tokens:
        if t in text:
            matched += 1
        elif t in _ALIAS_MAP:
            matched += any(a in text for a in _ALIAS_MAP[t])
    if matched == 0:
        return -5.0  # entity completely absent from result
    # Threshold: 10% of entity tokens must match. Lower than the prior 20% because
    # long geo/temporal queries (14+ tokens) have many framing words that won't appear
    # in short snippets — requiring only 1-2 core disease/topic tokens is sufficient.
    if len(entity_tokens) >= 2 and matched < len(entity_tokens) * 0.10:
        return -2.0
    return 0.0


def _path_intent_score(query: str, item: SearchSnippet, domain: str = "") -> float:
    """Prefer the right page shape within an authority domain.

    CRITICAL: All path boosts are gated by topical alignment — a COVID dashboard
    only gets boosted for queries that mention COVID-related terms. Without this
    gate, generic health queries like "mpox cases" score the COVID dashboard
    above authoritative mpox pages (e.g. WHO situation reports).
    """
    if domain != "health" or not item.url:
        return 0.0

    from urllib.parse import urlparse as _urlparse

    try:
        parsed = _urlparse(item.url.lower())
        netloc = parsed.netloc.removeprefix("www.")
        path = parsed.path or "/"
    except Exception:
        return 0.0

    q = query.lower()
    wants_cases = any(term in q for term in ("case", "cases", "prevalence", "incidence"))
    wants_deaths = any(term in q for term in ("death", "deaths", "mortality"))
    wants_surveillance = any(
        term in q for term in ("surveillance", "tracker", "dashboard", "latest", "current")
    )

    score = 0.0

    # COVID dashboards — only boost when query mentions COVID
    # Without this gate: "mpox cases" → COVID dashboard (+4.0) → ranked above mpox pages
    query_is_covid = any(term in q for term in ("covid", "covid-19", "sars-cov-2", "coronavirus", "covid19"))

    if _matches_domain_or_subdomain(netloc, "data.who.int"):
        if "/dashboards/covid19/cases" in path:
            score += 4.0 if (wants_cases and query_is_covid) else 0.0
        elif "/dashboards/covid19/deaths" in path:
            score += 1.0 if (wants_deaths and query_is_covid) else 0.0
        elif "/dashboards/covid19/" in path and query_is_covid:
            score += 1.5

    # WHO parent domain is too broad — only specific subdomains are authoritative.
    # `www.who.int` covers main pages; `who.int/emergencies` covers outbreak pages.
    # `data.who.int` (COVID/MERS dashboards), `cdn.who.int` (CDN), and
    # `www.who.int/news`, `www.who.int/health-topics` are NOT authoritative
    # for epidemiological queries.
    if netloc in ("who.int", "www.who.int") or netloc.startswith("who.int/emergencies"):
        if "epidemiological-update" in path or "weekly-epidemiological-update" in path:
            score += 2.5
        elif "situation-reports" in path or "disease-outbreak-news" in path:
            score += 1.5
        elif "/publications/m/item/" in path:
            score += 1.5

    if _matches_domain_or_subdomain(netloc, "cdc.gov"):
        if "/covid/php/surveillance/" in path:
            score += 3.5 if (wants_surveillance or wants_cases) and query_is_covid else 0.0
        elif "/covid-data-tracker" in path and query_is_covid:
            score += 2.5
        elif "/chickenpox/php/" in path:
            if any(term in q for term in ("chickenpox", "varicella", "shingles")):
                score += 3.5 if wants_surveillance or wants_cases else 2.0
        elif "/d/" in path or netloc.startswith("data.cdc.gov"):
            score -= 3.0

    if _matches_domain_or_subdomain(netloc, "ourworldindata.org"):
        if "/coronavirus" in path and query_is_covid:
            score += 2.5
        elif "/explorers/coronavirus-data-explorer" in path and query_is_covid:
            score += 2.0
        elif "/covid-cases" in path:
            score += 2.5 if (wants_cases and query_is_covid) else 0.0

    return score



_BEFORE_RE = re.compile(r"\b(before|prior to|until|leading up to)\b", re.I)
_AFTER_RE = re.compile(r"\b(after|following|since)\b", re.I)


def _relative_temporal_penalty(query: str, item: SearchSnippet) -> float:
    """Penalize items that likely violate relative temporal constraints (before/after)."""
    q = query.lower()
    text = f"{(item.title or '').lower()} {(item.snippet or '').lower()}"

    # Extract years from query and snippet using a local regex to ensure availability
    _Y_RE = re.compile(r"\b(20\d{2})\b")
    query_years = [int(y) for y in _Y_RE.findall(q)]
    snippet_years = [int(y) for y in _Y_RE.findall(text)]

    if not query_years or not snippet_years:
        return 0.0

    target_year = query_years[0]
    snippet_max_year = max(snippet_years)
    snippet_min_year = min(snippet_years)

    # "before 2026" but snippet only mentions 2026 or later
    if _BEFORE_RE.search(q) and snippet_min_year >= target_year:
        return -20.0 # Increased penalty to ensure disqualification in _result_has_content

    # "after 2025" but snippet only mentions 2025 or earlier
    if _AFTER_RE.search(q) and snippet_max_year <= target_year:
        return -20.0

    return 0.0


def _heuristic_rerank(
    query: str, items: list[SearchSnippet], limit: int, domain: str = "", temporal: bool = False
) -> list[SearchSnippet]:
    from tools.search_agentic._scoring import _embed_for_rerank, _semantic_score

    item_vecs: list[list[float] | None] = [None] * len(items)
    query_vec = None
    if len(items) > 1:
        vecs = _embed_for_rerank([query] + [f"{i.title} {i.snippet}" for i in items])
        query_vec, item_vecs = vecs[0], vecs[1:]

    def _scored(idx: int, item: SearchSnippet) -> float:
        authority = _authority_score(item)
        # Decoy/squatter domains and raw search-result pages are hard-blacklisted at -10
        # (see _authority_score) — a high textual/semantic match to the query is exactly
        # what a squatter domain optimizes for, so letting the semantic bonus apply there
        # would rescue precisely the pages the blacklist exists to filter out.
        semantic = 0.0 if authority <= -10.0 else _semantic_score(query_vec, item_vecs[idx])
        return (
            _query_overlap_score(query, item)
            + _freshness_bonus(item, temporal=temporal)
            + _noise_penalty(item)
            + authority
            + _domain_allow_score(item, domain)
            + _path_intent_score(query, item, domain)
            + _entity_lock_penalty(query, item)
            + _relative_temporal_penalty(query, item)
            + semantic
        )

    adjusted = [(_scored(idx, item), item) for idx, item in enumerate(items)]
    sorted_items = sorted(adjusted, key=lambda x: x[0], reverse=True)

    # If every result was penalised by entity lock, warn but still return them.
    # Returning empty is worse than returning penalized results — short snippets often
    # don't surface all entity tokens even for on-topic pages (e.g. 14-token geo/temporal
    # queries). The model can judge relevance; we cannot assume all results are wrong.
    has_site_constraint = bool(re.search(r"site:", query, re.I))
    entity_tokens = _extract_query_entity_tokens(query)
    if entity_tokens and sorted_items and not has_site_constraint:
        all_failed_entity = all(_entity_lock_penalty(query, item) < 0 for _, item in sorted_items)
        if all_failed_entity:
            _log.warning(
                "rerank: all %d results failed entity lock for query %r — returning penalized results",
                len(sorted_items),
                query[:80],
            )

    # Drop near-zero-overlap results when higher-quality ones exist.
    # Prevents off-topic items (e.g. Wikipedia pages on unrelated topics)
    # from contaminating the evidence block.
    if len(sorted_items) > 1:
        top_score = sorted_items[0][0]
        min_score = max(1.0, top_score * 0.25)  # at least 25% of best score, floor 1.0
        filtered = [(s, item) for s, item in sorted_items if s >= min_score]
        if len(filtered) >= 1:
            sorted_items = filtered

    final_items = [item for _, item in sorted_items]
    return final_items[:limit]


def _content_recency_bonus(item: SearchSnippet) -> float:
    """Bonus based on the most recent year mentioned inside the snippet/title text.
    Rewords items whose content discusses recent events, regardless of publication date.
    For current year content: DOMINATE bonus to outrank ALL stale sources.
    """
    current_year = _dt.date.today().year
    text = f"{item.snippet} {item.title}"
    years = [int(m) for m in _CONTENT_YEAR_RE.findall(text)]
    if not years:
        return 0.0
    most_recent = max(years)
    gap = current_year - most_recent
    if gap <= 0:
        return 50.0  # DOMINATE: current year content beats ALL stale sources (Wikipedia, etc.)
    if gap == 1:
        return 15.0  # last year — still relevant for recent queries
    if gap == 2:
        return 3.0
    return 0.0


def _freshness_bonus(item: SearchSnippet, temporal: bool = False) -> float:
    published = _parse_date(item.published_date or item.snippet or item.title)
    if not published:
        pub_bonus = 0.0
    else:
        age_days = (_dt.date.today() - published).days
        base = _freshness_decay(age_days)
        pub_bonus = base * 2.0 if temporal else base
    content_bonus = _content_recency_bonus(item) if temporal else 0.0
    return pub_bonus + content_bonus


def _noise_penalty(item: SearchSnippet) -> float:
    snippet = item.snippet or ""
    if any(m in snippet for m in _JS_MARKERS):
        return -10.0
    if _is_nav_noise(snippet):
        return -10.0
    return 0.0


_TEMPORAL_INTENTS = frozenset(
    {"temporal", "live_data", "live", "temporal_news", "temporal_finance", "temporal_sports"}
)


def rerank_items(
    query: str,
    items: list[SearchSnippet | dict],
    limit: int = 3,
    domain: str = "",
    intent: str = "",
) -> list[SearchSnippet]:
    normalized = [_coerce_item(item) for item in items]
    normalized = [i for i in normalized if not _should_deny(i, query)]  # deny filter

    if not normalized:
        return []

    temporal = _is_temporal(query) or bool(intent and any(t in intent for t in _TEMPORAL_INTENTS))
    return _heuristic_rerank(query, normalized, limit=limit, domain=domain, temporal=temporal)


def _evidence_block(
    items: list[SearchSnippet], max_snippet: int = 300, max_total: int = 2000
) -> str:
    parts = []
    total = 0
    for idx, item in enumerate(items, start=1):
        title = _clean(item.title) or f"Result {idx}"
        snippet = (_clean(item.snippet) or "")[:max_snippet]
        meta = f" | {item.published_date}" if item.published_date else ""
        link = f" | {item.url}" if item.url else ""
        entry = f"[{idx}] {title}{meta}{link}\n{snippet}"
        if total + len(entry) > max_total:
            break
        parts.append(entry)
        total += len(entry)
    return "\n\n".join(parts)


def _extract_answer(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    # Strip think blocks (qwen3 models wrap thinking in <think>...</think>)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Handle orphan </think> without opening tag
    if text.startswith("</think>"):
        text = text[len("</think>"):].strip()
    match = re.search(r"(?:^|\n)\s*(?:ANSWER|Answer|FINAL|Final):\s*(.+)", text, re.DOTALL)
    if match:
        return _clean(match.group(1))
    for prefix in ("ANSWER:", "Answer:", "FINAL:", "Final:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return _clean(text)


def _extract_large_numeric_claims(text: str) -> set[int]:
    claims: set[int] = set()
    for match in _LARGE_NUMERIC_CLAIM_RE.finditer(text or ""):
        raw_value = match.group(1).replace(",", "")
        unit = (match.group(2) or "").lower()
        try:
            value = float(raw_value)
        except ValueError:
            continue
        if unit == "million":
            claims.add(int(round(value * 1_000_000)))
        elif unit == "billion":
            claims.add(int(round(value * 1_000_000_000)))
        elif "." not in raw_value and len(raw_value) >= 5:
            claims.add(int(raw_value))
    return claims

_FORMAT_RE = re.compile(
    r"\b(csv|table|list|bullet|ranked|compare|recommend|sort|breakdown|side.?by.?side|benefits?|"
    r"advantages?|disadvantages?|features?|specs?|specifications?)\b", re.I
)
_FORMAT_PRIORITY = ["csv", "table", "list", "bullet", "compare", "recommend", "ranked", "sort", "breakdown", "benefits", "advantages", "disadvantages", "features", "specs", "specifications"]

# Explanation-intent queries: "explain", "why", "how does/do", "what is the difference", "in detail/depth/full"
_EXPLAIN_RE = re.compile(
    r"\b(explain|why|how\s+(?:does|do|is|are|can|should|would|could)"
    r"|what\s+is\s+the\s+difference|difference\s+between"
    r"|in\s+detail|in\s+depth|in\s+full|describe|overview|breakdown\s+of)\b",
    re.I,
)

# Implicit multi-item: "top 3", "top five", "3 headlines", "how do X and Y differ/compare"
_MULTI_ITEM_RE = re.compile(
    r"\b(top\s+\d+|top\s+(two|three|four|five|six|seven|eight|nine|ten)"
    r"|\d+\s+(headlines?|results?|examples?|reasons?|ways?|items?|tips?|points?|options?|benefits?)"
    r"|how\s+do\s+.{3,40}\s+(differ|compare|contrast)"
    r"|differences?\s+between"
    r"|pros?\s+and\s+cons?"
    r"|what\s+are\s+(the\s+)?\w+(?:\s+\w+)?\s+(benefits?|advantages?|disadvantages?)"
    r"|benefits?\s+of"
    r"|recommended?\s+\w+(?:\s+\w+)?"
    r"|best\s+\w+(?:\s+\w+)?\s+to\s+(learn|use|know|start|choose)"
    r"|recommended?\s+\w+(?:\s+\w+)?\s+to\s+learn"
    r"|which\s+\w+(?:\s+\w+)?\s+(is|are)\s+(better|best|faster|cheaper)"
    r"|health\s+benefits?)\b",
    re.I,
)


def _extract_format_hint(query: str) -> str | None:
    """Extract user-specified output format from the query. Returns canonical hint or None."""
    matches = {m.group(1).lower() for m in _FORMAT_RE.finditer(query)}
    for fmt in _FORMAT_PRIORITY:
        if fmt in matches:
            return fmt
    if _MULTI_ITEM_RE.search(query):
        return "multi"
    if _EXPLAIN_RE.search(query):
        return "explain"
    return None


# Matches: "10:00-20:00", "10:00 – 20:00", "10 AM - 8 PM", "10am to 8pm", "10h00-20h00"
_HOURS_RANGE_RE = re.compile(
    r"(\d{1,2})(?::(\d{2})|h(\d{2}))?\s*(am|pm)?\s*[-–to]+\s*(\d{1,2})(?::(\d{2})|h(\d{2}))?\s*(am|pm)?",
    re.I,
)
_CLOSE_TIME_RE = re.compile(
    r"(?:until|closes?\s*(?:at)?|open\s+until|last\s+entry\s*(?:at)?)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
    re.I,
)
_OPEN_TIME_RE = re.compile(
    r"(?:opens?\s*(?:at|from)?|from)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
    re.I,
)


def _parse_hhmm(h: str, m: str | None, ampm: str | None) -> int:
    """Return minutes-since-midnight, or -1 on failure."""
    try:
        hh = int(h)
        mm = int(m) if m else 0
        if ampm:
            ampm = ampm.lower()
            if ampm == "pm" and hh != 12:
                hh += 12
            elif ampm == "am" and hh == 12:
                hh = 0
        # Sanity check — reject obviously invalid times
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return -1
        return hh * 60 + mm
    except Exception:
        return -1


def _venue_open_status(current_time_str: str, evidence_text: str) -> str | None:
    """
    Parse opening/closing hours from evidence text and compare with current time.
    Returns a hard status string like "CLOSED (closed at 20:00)" or None if unparseable.
    """
    try:
        ch, cm = map(int, current_time_str.split(":"))
    except Exception:
        return None
    now_mins = ch * 60 + cm

    # Try HH:MM–HH:MM / H AM–H PM range first (most unambiguous)
    # Groups: (open_h, open_m_colon, open_m_h, open_ampm, close_h, close_m_colon, close_m_h, close_ampm)
    for m in _HOURS_RANGE_RE.finditer(evidence_text):
        oh = m.group(1)
        om = m.group(2) or m.group(3) or "00"  # :MM or hMM
        o_ampm = m.group(4)
        clh = m.group(5)
        clm = m.group(6) or m.group(7) or "00"
        cl_ampm = m.group(8)
        open_mins = _parse_hhmm(oh, om, o_ampm)
        close_mins = _parse_hhmm(clh, clm, cl_ampm)
        if open_mins < 0 or close_mins < 0 or close_mins <= open_mins:
            continue
        open_fmt = f"{open_mins // 60:02d}:{open_mins % 60:02d}"
        close_fmt = f"{close_mins // 60:02d}:{close_mins % 60:02d}"
        if now_mins < open_mins:
            return f"NOT YET OPEN — opens at {open_fmt}"
        elif now_mins >= close_mins:
            return f"CLOSED — it closed at {close_fmt} today"
        else:
            return f"OPEN — closes at {close_fmt}"

    # Fall back to "closes at X" pattern
    cm_match = _CLOSE_TIME_RE.search(evidence_text)
    if cm_match:
        close_mins = _parse_hhmm(cm_match.group(1), cm_match.group(2), cm_match.group(3))
        if close_mins > 0:
            close_fmt = f"{close_mins // 60:02d}:{close_mins % 60:02d}"
            if now_mins >= close_mins:
                return f"CLOSED — it closed at {close_fmt} today"
            else:
                return f"OPEN — closes at {close_fmt}"

    return None


_CITY_TZ_MAP: dict[str, str] = {
    "san francisco": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "portland": "America/Los_Angeles",
    "new york": "America/New_York",
    "boston": "America/New_York",
    "miami": "America/New_York",
    "washington": "America/New_York",
    "chicago": "America/Chicago",
    "dallas": "America/Chicago",
    "houston": "America/Chicago",
    "denver": "America/Denver",
    "london": "Europe/London",
    "tokyo": "Asia/Tokyo",
    "sydney": "Australia/Sydney",
    "singapore": "Asia/Singapore",
    "dubai": "Asia/Dubai",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "mumbai": "Asia/Kolkata",
}

_TIMEZONE_Q_RE = re.compile(
    r"(\d{1,2}(?::\d{2})?)\s*(am|pm)\s+in\s+([A-Za-z][a-zA-Z\s]{2,})",
    re.I,
)

_TZ_TARGETS = [
    ("New York", "America/New_York"),
    ("London", "Europe/London"),
    ("Tokyo", "Asia/Tokyo"),
    ("Sydney", "Australia/Sydney"),
    ("Singapore", "Asia/Singapore"),
]


def _compute_timezone_context(query: str) -> str | None:
    """
    Detect a time + city phrase in the query and return a [TIMEZONE CONTEXT] prefix
    with conversions to major zones. Degrades gracefully — any failure returns None.
    """
    try:
        from zoneinfo import ZoneInfo
        import datetime as _dtz

        m = _TIMEZONE_Q_RE.search(query)
        if not m:
            return None
        time_raw, ampm, city_raw = (
            m.group(1),
            m.group(2).lower(),
            m.group(3).strip().lower().rstrip("?., "),
        )

        src_tz_name = _CITY_TZ_MAP.get(city_raw)
        if not src_tz_name:
            for k, v in _CITY_TZ_MAP.items():
                if city_raw.startswith(k) or k.startswith(city_raw):
                    src_tz_name = v
                    break
        if not src_tz_name:
            return None

        if ":" in time_raw:
            h, mn = (int(x) for x in time_raw.split(":"))
        else:
            h, mn = int(time_raw), 0
        if ampm == "pm" and h != 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0

        src_tz = ZoneInfo(src_tz_name)
        today = _dtz.date.today()
        src_dt = _dtz.datetime(today.year, today.month, today.day, h, mn, tzinfo=src_tz)

        parts = []
        for label, tz_name in _TZ_TARGETS:
            if tz_name == src_tz_name:
                continue
            tgt_dt = src_dt.astimezone(ZoneInfo(tz_name))
            date_note = (
                " (+1 day)"
                if tgt_dt.date() > today
                else (" (-1 day)" if tgt_dt.date() < today else "")
            )
            parts.append(
                f"{label} {tgt_dt.strftime('%-I:%M %p')} {tgt_dt.strftime('%Z')}{date_note}"
            )

        city_display = city_raw.title()
        src_fmt = src_dt.strftime("%-I:%M %p")
        src_abbrev = src_dt.strftime("%Z")
        return (
            f"[TIMEZONE CONTEXT] {src_fmt} {city_display} ({src_abbrev}) = "
            + " | ".join(parts)
            + "."
        )
    except Exception:
        return None


def summarize_with_model(
    query: str,
    items: list[SearchSnippet],
    intent: str = "",
    domain: str = "",
    timeout_s: float = 120.0,
    on_token=None,
    format_hint: str | None = None,
    model: str = "gemma4:12b-mlx",
) -> str:
    from clients import cloud_client as _cc
    _is_cloud = _cc.is_cloud_model(model)
    # NEW: Debug logging — see exactly what evidence is fed to the model
    _log.debug("summarize_with_model: query=%r intent=%s domain=%s items=%d", query[:80], intent, domain, len(items))
    for i, item in enumerate(items):
        _log.debug("  [%d] title=%r date=%s url=%s", i, (item.title or "")[:60], item.published_date, (item.url or "")[:60])
        snippet_preview = (item.snippet or "")[:150]
        _log.debug("      snippet=%r", snippet_preview)

    _now = _dt.datetime.now()
    _today = _now.date()
    today_str = _today.isoformat()

    fmt = format_hint or _extract_format_hint(query)
    _q_low = query.lower()
    is_schedule = any(
        w in _q_low
        for w in (
            "schedule", "scheduled", "fixture", "fixtures", "match", "matches",
            "game", "games", "lineup", "line-up", "playoff", "playoffs",
            "standings", "results", "scores", "agenda", "itinerary",
        )
    )
    # Default evidence budget; schedule/list queries get more so all rows survive.
    _max_snippet, _max_total = 500, 3000
    if is_schedule:
        format_rule = (
            "- Output a bullet list containing EVERY match/event found in the evidence for the date in the question.\n"
            "- One bullet per event: include the teams/participants, kickoff time, venue, and group/round when present.\n"
            "- Do NOT state a total count unless you also list each event individually below it.\n"
            "- EXCLUDE any event whose date differs from the date asked about — check each event's date carefully.\n"
            "- If NO event in the evidence matches that exact date, say so plainly; never substitute a different date's events.\n"
        )
        _max_snippet, _max_total = 900, 6000
    elif fmt in ("explain",):
        format_rule = (
            "- Provide a thorough explanation with sections if needed.\n"
            "- Use ## headers for main points, - bullets for details.\n"
            "- Include all relevant numbers, dates, and specifics from evidence.\n"
            "- Aim for 200-400 words when evidence is rich.\n"
        )
    elif fmt in ("compare", "ranked", "sort", "breakdown", "versus", "vs"):
        format_rule = (
            "- Present findings as a structured comparison. Use a table or side-by-side format if comparing specs.\n"
            "- List key differences and similarities.\n"
            "- Include specific numbers, prices, dates, or specs from the evidence.\n"
            "- Be detailed — aim for 150-300 words.\n"
        )
    elif fmt in ("multi", "list", "bullet", "recommend", "benefits", "advantages", "disadvantages", "features", "specs", "specifications"):
        format_rule = (
            "- Answer with a bullet list covering all items from the evidence.\n"
            "- Each bullet should be specific with facts, numbers, or details.\n"
            "- Include all relevant items — don't truncate the list.\n"
            "- Aim for 100-250 words.\n"
        )
    else:
        format_rule = (
            "- Provide a clear, direct answer using the evidence.\n"
            "- Include the single most relevant specific number, date, or fact.\n"
            "- Be concise — aim for 1-3 sentences, under 80 words.\n"
        )

    prompt = (
        f"Today's date: {today_str}.\n"
        "Answer the user's question using the evidence below.\n"
        "Rules:\n"
        + format_rule
        + "- Extract specific facts, numbers, dates, names, and versions from the evidence.\n"
        "- If the evidence contains a year, version, price, or number — state it directly.\n"
        "- If evidence is truly insufficient, say what was found.\n"
        "- If different pieces of evidence disagree on a specific number, score, or date (e.g. one "
        "source says 4-2, another says 3-2), do NOT silently pick one and state it as confirmed — "
        "say the sources disagree and give the range/values you saw instead.\n"
        "- If ANY piece of evidence indicates an event is still in progress (live, ongoing, "
        "\"in extra time\", not yet finished) while other evidence claims a final result, treat it "
        "as NOT yet finished — do not declare a winner, final score, or shootout result. Say the "
        "event may still be underway and results you found may be premature/unconfirmed.\n"
        "- Do not invent facts. Use Markdown.\n\n"
        f"Question: {query}\n\n"
        f"Evidence:\n{_evidence_block(items, max_snippet=_max_snippet, max_total=_max_total)}"
    )

    # --- Cloud model path ---
    if _is_cloud:
        sys_msg = "Synthesize search results into a clear, direct answer. Extract and state specific facts, numbers, dates, and names from the evidence."
        try:
            result = _cc.chat(
                user_message=f"{sys_msg}\n\n{prompt}",
                model=model,
                on_token=on_token,
                max_rounds=1,
            )
            return _extract_answer(result)
        except Exception as exc:
            _log.warning("cloud summarize failed: %s — falling back to deterministic", exc)
            return deterministic_summary(query, items)

    # --- Local (Ollama) streaming path ---
    if on_token:

        def _stream_with_token() -> str:
            import ollama

            content_parts: list[str] = []
            for chunk in ollama.chat(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "Synthesize search results into a clear, direct answer. Extract and state specific facts, numbers, dates, and names from the evidence.",
                    },
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": 0.1, "top_p": 0.9, "num_ctx": 16384, "num_predict": 1024},
                keep_alive=-1,
                think=False,
                stream=True,
            ):
                check_aborted()
                text = chunk.message.content or ""
                if text:
                    content_parts.append(text)
                    on_token(text)
            return "".join(content_parts).lstrip()

        _ex = ThreadPoolExecutor(max_workers=1)
        _f = _ex.submit(_stream_with_token)
        try:
            raw = _f.result(timeout=timeout_s)
        except FuturesTimeout:
            _safe_shutdown(_ex)
            _log.warning(
                "summarize_with_model: timed out after %.1fs — falling back to deterministic summary",
                timeout_s,
            )
            return deterministic_summary(query, items)
        except Exception as exc:
            _safe_shutdown(_ex)
            _log.warning(
                "summarize_with_model: error — %s — falling back to deterministic summary", exc
            )
            return deterministic_summary(query, items)
        _safe_shutdown(_ex)
        return _extract_answer(raw)

    def _call() -> str:
        import ollama

        resp = ollama.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "Synthesize search results into a clear, direct answer. Extract and state specific facts, numbers, dates, and names from the evidence.",
                },
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.1, "top_p": 0.9, "num_ctx": 16384, "num_predict": 1024},
            keep_alive=-1,
            think=False,
            stream=False,
        )
        return resp.message.content or ""

    _ex = ThreadPoolExecutor(max_workers=1)
    _f = _ex.submit(_call)
    try:
        raw = _f.result(timeout=timeout_s)
    except FuturesTimeout:
        _safe_shutdown(_ex)
        _log.warning(
            "summarize_with_model: timed out after %.1fs — falling back to deterministic summary",
            timeout_s,
        )
        return deterministic_summary(query, items)
    except Exception as exc:
        _safe_shutdown(_ex)
        _log.warning(
            "summarize_with_model: error — %s — falling back to deterministic summary", exc
        )
        return deterministic_summary(query, items)
    _safe_shutdown(_ex)
    return _extract_answer(raw)


def deterministic_summary(query: str, items: list[SearchSnippet]) -> str:
    if _source_quality_score(items) < _QUALITY_THRESHOLD:
        return (
            f"No reliable sources found for: {query}. "
            "Try a more specific query or check a specialised site."
        )
    top = items[0] if items else SearchSnippet()
    snippet = _clean(top.snippet)
    title = _clean(top.title)
    if not snippet and title:
        snippet = title
    if not snippet:
        return f"Answer: I couldn't find a reliable exact answer for: {query}"
    if len(snippet) > 320:
        snippet = snippet[:317].rstrip() + "..."
    return f"Answer: {snippet}"


# ── Multi-hop query decomposition ────────────────────────────────────────────

_MULTIHOP_RE = re.compile(
    r"(?P<hop1>\b(?:which|what|who)\b.{3,80}"
    r"\b(?:shortest|longest|most|least|best|worst|first|last|highest|lowest)\b"
    r".{3,60})"
    r"(?P<conj>[,]\s*(?:and\s+)?|and\s+)"
    r"(?P<hop2>(?:who|what|where|when|its|their|his|her)\b.{3,150})",
    re.I | re.S,
)

_ENTITY_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")
_ENTITY_SKIP = frozenset(
    {
        "The",
        "A",
        "An",
        "In",
        "Of",
        "On",
        "At",
        "To",
        "By",
        "For",
        "With",
        "And",
        "Or",
        "But",
        "Is",
        "Are",
        "Was",
        "Were",
        "Be",
        "It",
        "Its",
        "This",
        "That",
        "These",
        "Those",
        "Their",
        "Which",
        "Who",
        "What",
        "When",
        "Where",
        "How",
        "As",
    }
)

_QUOTED_RE = re.compile(r'["\u201c\u201d]([^""\u201c\u201d]{2,60})["\u201c\u201d]')
_BY_RE = re.compile(r"\bby\s+([A-Z][A-Za-z\s]{1,40}?)(?:[,\.]|$)", re.M)
_IS_RE = re.compile(
    r'\bis\s+["\u201c]?([A-Z][A-Za-z\s]{2,50}?)["\u201d]?(?:\s+by|\s+directed|[,\.]|$)'
)