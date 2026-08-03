"""Agentic post-search pipeline — normalization, scoring, and reranking helpers."""


from __future__ import annotations

import datetime as _dt
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from functools import lru_cache

_log = logging.getLogger(__name__)

from tools.search_result import SearchResult, SearchSnippet
from clients.cancellation import check_aborted

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "did",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "latest",
    "me",
    "news",
    "of",
    "on",
    "or",
    "recent",
    "show",
    "tell",
    "the",
    "to",
    "today",
    "upcoming",
    "was",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.+:-]*", re.I)
_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_NAMED_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),?\s+(20\d{2})\b",
    re.I,
)
_RELATIVE_DATE_RE = re.compile(r"\b(\d+)\s+(hour|day|week|month)s?\s+ago\b", re.I)
_LARGE_NUMERIC_CLAIM_RE = re.compile(
    r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(million|billion)?\b",
    re.I,
)
_CONTENT_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_MONTH_MAP = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_TEMPORAL_RE = re.compile(
    r"\b(today|tonight|right now|current(ly)?|latest|most recent|recent(ly)?|newest|"
    r"next|upcoming|scheduled|slated|when is|when will|launch date|release date|"
    r"this (week|month|year|morning|evening|afternoon)|"
    r"breaking|just (now|happened|announced|released)|"
    r"live\b|real.?time|as of (today|now)|"
    r"who (won|is winning|leads)|who.s (winning|leading)|"
    r"what.s (happening|going on)|news today|update today|"
    # Weather / election — freshness changes hourly/daily
    r"weather|temperature|forecast|storm|rain|election|"
    # Date-anchored: "as of April 16 2026", "as of 2026"
    r"as of \w+ \d{1,2}|as of \d{4}|"
    # Celestial / ephemeris — change hourly
    r"moon phase|lunar (phase|distance|illumination)|moon.?s? distance|"
    r"sunrise|sunset|moonrise|moonset|tide(s)?|tidal|"
    r"planet (position|alignment)|solar eclipse|lunar eclipse|"
    # Software versioning — latest release is always live
    r"latest (stable )?version|latest (stable )?release|latest (stable )?kernel|"
    r"stable (release|version) of|kernel version)\b",
    re.I,
)


def _is_temporal(query: str) -> bool:
    """Return True if the query asks for data that may have changed recently."""
    return bool(_TEMPORAL_RE.search(query))


_PRICING_MATH_RE = re.compile(
    r"\b(cheaper|which\s+(is|costs?)\s+(less|more)|annual\s+cost|"
    r"over\s+a\s+(full\s+)?year|monthly\s+vs|discount\s+on\s+annual|"
    r"save\s+over|annual\s+billing|total\s+(cost|price)\s+per\s+year)\b",
    re.IGNORECASE,
)

_LOCAL_CRITERIA_RE = re.compile(
    r"\b(open\s+until|closes?\s+at|open\s+on\s+\w+day|"
    r"outdoor\s+seating|patio|rooftop|"
    r"vegan|vegetarian|gluten.free|halal|kosher|"
    r"highly.rated|top.rated)\b",
    re.IGNORECASE,
)


def _freshness_decay(age_days: int) -> float:
    """Smooth linear decay from 2.0 at 0 days to 0.0 at 60 days."""
    if age_days < 0:
        return 0.0
    return max(0.0, 2.0 * (1.0 - age_days / 60.0))


_QUALITY_THRESHOLD = float(os.environ.get("G4L_SEARCH_QUALITY_THRESHOLD", "0.3"))
_MIN_USEFUL_SNIPPET_LEN = 60  # chars — snippets shorter than this are nav junk or too vague


def _source_quality_score(items: list) -> float:
    """
    Returns 0.0-1.0 quality score for a result set.
    Score < _QUALITY_THRESHOLD means no reliable answer should be synthesized.
    Accepts both SearchSnippet objects and plain dicts.
    """
    if not items:
        return 0.0

    def _get_snippet(item) -> str:
        if isinstance(item, dict):
            return item.get("snippet") or ""
        return item.snippet or ""

    usable = [i for i in items if len(_get_snippet(i).strip()) >= _MIN_USEFUL_SNIPPET_LEN]
    if not usable:
        return 0.0
    score = sum(min(len(_get_snippet(i).strip()), 300) / 300 for i in usable) / len(items)
    return min(1.0, score)


# Markers that indicate inline JS leaked into a snippet (GTM, analytics, etc.)
_JS_MARKERS = (
    "(function(",
    "!function(",
    "function(w,d,s",
    "{gtm.start:",
    "window.dataLayer",
    "w[l]=w[l]||",
)

# Patterns that indicate site navigation / footer noise scraped as snippet content
_NAV_MARKERS = (
    "News Categories",
    "### ",
    "- Company News",
    "- Industry News",
    "Featured News",
    "Languages\n",
    "\nLanguages\n",
)

# ── Authority scoring ─────────────────────────────────────────────────────────

_TLD_AUTHORITY: dict[str, float] = {
    ".gov": 3.0,
    ".edu": 2.5,
    ".ac.uk": 2.5,
    ".ac.": 2.0,
    ".us": 1.5,
    ".org": 0.3,
}

_STATE_HEALTH_DEPT_RE = re.compile(r"^health\.state\.[a-z]{2}\.us$")


def _is_state_health_dept(url: str) -> bool:
    """Return True for state health department URLs (e.g. health.state.mn.us)."""
    try:
        from urllib.parse import urlparse as _urlparse

        netloc = _urlparse((url or "").lower()).netloc.removeprefix("www.")
        return bool(_STATE_HEALTH_DEPT_RE.match(netloc))
    except Exception:
        return False


_HIGH_AUTHORITY_DOMAINS = frozenset(
    {
        "reuters.com",
        "bbc.com",
        "bbc.co.uk",
        "apnews.com",
        "npr.org",
        "bloomberg.com",
        "ft.com",
        "wsj.com",
        "nytimes.com",
        "theguardian.com",
        "who.int",
        "cdc.gov",
        "nih.gov",
        "nature.com",
        "science.org",
        "pubmed.ncbi.nlm.nih.gov",
        "ourworldindata.org",
        "github.com",
        "docs.python.org",
        "developer.mozilla.org",
        "stackoverflow.com",
        "arxiv.org",
        # Primary sources not covered by .gov/.edu TLD boost
        "support.apple.com",
        "developer.apple.com",  # Apple security notes, dev docs
        "airbnb.com",  # Official ToS, policy pages
        "msrc.microsoft.com",  # Microsoft Security Response Center
        "learn.microsoft.com",
        "docs.microsoft.com",  # Microsoft docs
        "support.google.com",
        "developers.google.com",  # Google support/docs
        "jpx.co.jp",  # Tokyo Stock Exchange official
        "sony.com",  # Sony official product pages
        "fifa.com",
        "concacaf.com",
        "uefa.com",
        "olympics.com",
        "haitiantimes.com",
    }
)

# Tuple (not frozenset) because matching uses endswith(), not equality.
_LOW_AUTHORITY_PATTERNS = (".xyz", ".tk", ".click", ".loan", ".win", ".gq", ".cf")


def _matches_domain_or_subdomain(netloc: str, domain: str) -> bool:
    return netloc == domain or netloc.endswith("." + domain)


_DECOY_DOMAINS = {"sportbusy.com", "fifa-worldcup26.com"}


def _semantic_score(query_vec: list[float] | None, item_vec: list[float] | None) -> float:
    """Cosine-similarity bonus between query and item embeddings, via nomic-embed (already-installed
    embedder, reused rather than pulling in a dedicated cross-encoder). Scaled small (max ~3) so it
    nudges the heuristic ranking rather than overriding it — token overlap/freshness/authority still
    dominate.
    """
    if query_vec is None or item_vec is None:
        return 0.0
    import numpy as np

    sim = float(np.dot(query_vec, item_vec))  # both L2-normalized -> cosine sim in [-1, 1]
    return max(0.0, sim) * 3.0


def _embed_for_rerank(texts: list[str], timeout_s: float = 3.0) -> list[list[float] | None]:
    """Best-effort parallel embed of query + item texts, one shared timeout budget.
    Ollama down/slow -> all None, rerank falls back to the pre-existing heuristic score only.
    """
    from store.knowledge_base import _embed as _kb_embed

    ex = ThreadPoolExecutor(max_workers=min(len(texts), 8))
    try:
        futures = [ex.submit(_kb_embed, t[:1024]) for t in texts]
        results: list[list[float] | None] = []
        for f in futures:
            try:
                results.append(f.result(timeout=timeout_s))
            except Exception:
                results.append(None)
        return results
    finally:
        ex.shutdown(wait=False)


def _authority_score(item: SearchSnippet) -> float:
    from urllib.parse import urlparse as _urlparse

    url = (item.url or "").lower()
    if not url:
        return 0.0
    try:
        parsed = _urlparse(url)
        domain = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path
        query = parsed.query
    except Exception:
        return 0.0

    # 1. Penalize decoy/squatter domains globally
    if domain in _DECOY_DOMAINS or any(d in domain for d in _DECOY_DOMAINS):
        return -10.0

    # 2. Penalize search result / query pages globally
    if "/search" in path or "query=" in query or "q=" in query:
        return -10.0

    # TLD authority takes precedence over high-authority domain match —
    # e.g. cdc.gov scores 3.0 (via .gov TLD) not 2.0 (via _HIGH_AUTHORITY_DOMAINS).
    for tld, score in _TLD_AUTHORITY.items():
        if domain.endswith(tld):
            return score
    for authority_domain in _HIGH_AUTHORITY_DOMAINS:
        if _matches_domain_or_subdomain(domain, authority_domain):
            return 2.0
    for pattern in _LOW_AUTHORITY_PATTERNS:
        if domain.endswith(pattern):
            return -2.0
    if _is_state_health_dept(item.url or ""):
        return 3.0
    return 0.0


# ── Domain allow/deny rules ───────────────────────────────────────────────────

# Domains to exclude entirely — low-quality aggregators for factual queries.
# frozenset OK here because matching is equality, not suffix.
_DOMAIN_DENY = frozenset(
    {
        "pinterest.com",
        "quora.com",
        # Live sports schedules/scores — not useful for factual event queries
        "livesoccertv.com",
        "sofascore.com",
        "soccerway.com",
        "flashscore.com",
        "livescore.com",
        "footystats.org",
        "soccerstats.com",
        "fbref.com",
        "soccerbase.com",
    }
)

# Per-category allow lists — sources that get a score boost (not a hard filter).
_DOMAIN_ALLOW_BOOST: dict[str, frozenset] = {
    "finance": frozenset({"bloomberg.com", "reuters.com", "wsj.com", "ft.com", "marketwatch.com"}),
    "sports": frozenset(
        {
            "espn.com",
            "bbc.co.uk",
            "bbc.com",
            "goal.com",
            "skysports.com",
            "theathletic.com",
            "fifa.com",
            "concacaf.com",
            "uefa.com",
            "olympics.com",
            "haitiantimes.com",
        }
    ),
    "news": frozenset(
        {"apnews.com", "reuters.com", "bbc.com", "bbc.co.uk", "theguardian.com", "npr.org"}
    ),
    "code": frozenset(
        {"github.com", "stackoverflow.com", "docs.python.org", "developer.mozilla.org", "pypi.org"}
    ),
    "health": frozenset(
        {
            "cdc.gov",
            "who.int",
            "nih.gov",
            "ourworldindata.org",
            "nejm.org",
            "bmj.com",
            "thelancet.com",
            "pubmed.ncbi.nlm.nih.gov",
        }
    ),
}


def _should_deny(item: SearchSnippet, query: str = "") -> bool:
    from urllib.parse import urlparse as _urlparse

    url = (item.url or "").lower()
    if not url:
        return False
    try:
        netloc = _urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return False
    if query:
        q_lower = query.lower()
        if "pinterest" in q_lower and "pinterest.com" in netloc:
            return False
        if "quora" in q_lower and "quora.com" in netloc:
            return False
    return netloc in _DOMAIN_DENY



def _domain_allow_score(item: SearchSnippet, domain: str) -> float:
    from urllib.parse import urlparse as _urlparse

    if not domain or domain not in _DOMAIN_ALLOW_BOOST:
        return 0.0
    url = (item.url or "").lower()
    if not url:
        return 0.0
    try:
        netloc = _urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return 0.0
    return (
        1.5
        if any(_matches_domain_or_subdomain(netloc, d) for d in _DOMAIN_ALLOW_BOOST[domain])
        else 0.0
    )

def _strip_js(text: str) -> str:
    """Truncate snippet at the first JS or nav-noise marker, then strip trailing junk."""
    for marker in _JS_MARKERS + _NAV_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx].rstrip(" —|;,")
    return text.strip()


def _is_nav_noise(text: str) -> bool:
    """Return True if the snippet is mostly site navigation / footer boilerplate."""
    if text.count("###") >= 2:
        return True
    if sum(1 for m in _NAV_MARKERS if m in text) >= 2:
        return True
    return False


def _clean(text: str) -> str:
    return " ".join((text or "").replace("\n", " ").split())


def _safe_shutdown(executor) -> None:
    shutdown = getattr(executor, "shutdown", None)
    if shutdown:
        shutdown(wait=False, cancel_futures=True)


def _tokens(text: str) -> set[str]:
    return {
        tok.lower()
        for tok in _TOKEN_RE.findall(text or "")
        if tok and tok.lower() not in _STOPWORDS and len(tok) > 1
    }


def _parse_date(text: str) -> _dt.date | None:
    text = text or ""
    # ISO: 2026-03-01
    m = _DATE_RE.search(text)
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # Named: March 1, 2026
    m = _NAMED_DATE_RE.search(text)
    if m:
        try:
            month = _MONTH_MAP[m.group(1).lower()]
            return _dt.date(int(m.group(3)), month, int(m.group(2)))
        except (KeyError, ValueError):
            pass
    # Relative: "3 days ago", "1 week ago", "2 months ago"
    m = _RELATIVE_DATE_RE.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"hour": 0, "day": n, "week": n * 7, "month": n * 30}.get(unit, 0)
        return _dt.date.today() - _dt.timedelta(days=delta)
    return None


def _parse_items_from_content(content: str, source: str) -> list[SearchSnippet]:
    items: list[SearchSnippet] = []
    current: SearchSnippet | None = None
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Summary:"):
            items.append(
                SearchSnippet(
                    title="Summary",
                    url="",
                    snippet=_clean(line[len("Summary:") :]),
                    published_date="",
                    source=source,
                )
            )
            current = None
            continue
        if line.startswith("- "):
            title_part = line[2:]
            url = ""
            if " (" in title_part and title_part.endswith(")"):
                before, _, rest = title_part.rpartition(" (")
                maybe_url = rest[:-1]
                if maybe_url.startswith("http://") or maybe_url.startswith("https://"):
                    title_part = before
                    url = maybe_url
            current = SearchSnippet(
                title=_clean(title_part),
                url=url,
                snippet="",
                published_date="",
                source=source,
            )
            items.append(current)
            continue
        if current is not None:
            current.snippet = _clean(f"{current.snippet} {line}")
    return items


def _normalize_items(result: SearchResult) -> list[SearchSnippet]:
    items = result.items or []
    if not items:
        items = _parse_items_from_content(result.content, result.source)
    return items


def _coerce_item(item: SearchSnippet | dict) -> SearchSnippet:
    if isinstance(item, SearchSnippet):
        item.snippet = _strip_js(item.snippet or "")
        return item
    if isinstance(item, dict):
        return SearchSnippet(
            title=_clean(str(item.get("title", ""))),
            url=_clean(str(item.get("url", ""))),
            snippet=_strip_js(_clean(str(item.get("snippet", "")))),
            published_date=_clean(str(item.get("published_date", ""))),
            source=_clean(str(item.get("source", ""))),
        )
    return SearchSnippet()


def _tokenize(text: str) -> list[str]:
    return [
        tok.lower()
        for tok in _TOKEN_RE.findall(text or "")
        if tok and tok.lower() not in _STOPWORDS and len(tok) > 1
    ]


def _query_overlap_score(query: str, item: SearchSnippet) -> float:
    q = set(_tokenize(query))
    title = set(_tokenize(item.title))
    snippet_text = re.sub(r"^\[browser-rendered\]\s*", "", item.snippet or "", flags=re.I).strip()
    snippet = set(_tokenize(snippet_text))
    if not q:
        return 0.0
    title_hits = len(q & title)
    snippet_hits = len(q & snippet)
    phrase_bonus = (
        1.5 if _clean(query).lower() in _clean(f"{item.title} {item.snippet}").lower() else 0.0
    )
    return title_hits * 3.0 + snippet_hits * 1.0 + phrase_bonus


# Generic terms that carry no entity signal — filtered out before entity locking.
_ENTITY_STOPWORDS = frozenset(
    {
        # Core question words
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "how",
        # Copula / auxiliary verbs
        "are",
        "were",
        "is",
        "was",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "will",
        # Articles / determiners
        "the",
        "a",
        "an",
        # Prepositions / conjunctions
        "on",
        "in",
        "at",
        "to",
        "of",
        "for",
        "and",
        "or",
        "by",
        "with",
        "from",
        "as",
        "than",
        "then",
        "but",
        "if",
        "so",
        # Query helpers / framing words
        "latest",
        "numbers",
        "number",
        "data",
        "stats",
        "statistics",
        "figure",
        "figures",
        "count",
        "rate",
        "rates",
        "report",
        "reports",
        "update",
        "updates",
        "news",
        "info",
        "information",
        "today",
        "now",
        "this",
        "that",
        "these",
        "those",
        "here",
        "there",
        "year",
        "month",
        "week",
        "2023",
        "2024",
        "2025",
        "2026",
        "about",
        "many",
        "much",
        "some",
        "any",
        "more",
        "most",
        "best",
        "worst",
        "new",
        "last",
        "just",
        "only",
        "also",
        "very",
        "really",
        "actually",
        "please",
        "tell",
        "me",
        "give",
        "show",
        "find",
        "get",
        "ask",
        "want",
        "need",
        "use",
        "used",
        "using",
        # Affirmations / negations
        "yes",
        "no",
        "not",
        "none",
        "nor",
        # Framing / social context
        "according",
        "according to",
        "source",
        "sources",
        "rumors",
        "rumor",
        "social",
        "media",
        "confirmed",
        "reputable",
        "official",
        "latest",
        # Countries / regions (too generic for entity lock)
        "us",
        "usa",
        "united",
        "states",
        "america",
        "world",
        "global",
        "current",
        "recent",
        "recently",
    }
)


