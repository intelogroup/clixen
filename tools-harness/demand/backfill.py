"""Historical backfill: dated complaint items from HN and Stack Exchange, windowed by month/quarter.

Windows in the past never change, so requests go through the Fetcher's disk cache
(no force) and a re-run only fetches what is new.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from tsdata.http import Fetcher

from .pain import _clean, CACHE

OUT = CACHE.parent / "backfill.jsonl"
_HN = "https://hn.algolia.com/api/v1/search"
_SE = "https://api.stackexchange.com/2.3/search/advanced"


def windows(months: int, step: int = 1, today: date | None = None) -> list[tuple[int, int]]:
    """(start, end) epoch pairs of `step` months each, oldest first, ending at the start of this month."""
    t = today or date.today()
    first = date(t.year, t.month, 1)
    edges = []
    for i in range(months, -1, -step):
        y, m = divmod(first.year * 12 + first.month - 1 - i, 12)
        edges.append(int(datetime(y, m + 1, 1, tzinfo=timezone.utc).timestamp()))
    return list(zip(edges, edges[1:]))


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def hn_window(f: Fetcher, q: str, tag: str, a: int, b: int) -> list[dict]:
    d = f.get_json(_HN, params={"query": q, "tags": tag, "hitsPerPage": 100,
                                "numericFilters": f"created_at_i>={a},created_at_i<{b}"})
    out = []
    for h in d.get("hits", []):
        raw = h.get("comment_text") or h.get("story_text") or ""
        text = _clean(raw) if tag == "comment" else f'{h.get("title", "")} — {_clean(raw)}'
        if len(text) > 80 or tag != "comment":
            out.append({"url": f'https://news.ycombinator.com/item?id={h["objectID"]}',
                        "text": text[:700], "date": h["created_at"], "source": "hn" if tag == "comment" else "ask_hn"})
    return out


def se_window(f: Fetcher, q: str, a: int, b: int) -> list[dict]:
    d = f.get_json(_SE, params={"site": "softwarerecs", "q": q, "pagesize": 100, "sort": "creation",
                                "order": "desc", "filter": "withbody", "fromdate": a, "todate": b})
    return [{"url": i["link"], "text": f'{i["title"]} — {_clean(i.get("body"))}'[:700],
             "date": _iso(i["creation_date"]), "source": "softwarerecs"} for i in d.get("items", [])]


def backfill(queries: list[str], keywords: list[str], months: int = 24, out: Path = OUT,
             f: Fetcher | None = None, warn=print) -> list[dict]:
    """Fetch every window for every query/keyword, dedupe by URL, write `out` as JSONL."""
    f = f or Fetcher(CACHE)
    seen: dict[str, dict] = {}
    jobs = [(q, tag, w) for q in queries for tag in ("comment", "ask_hn") for w in windows(months)]
    jobs_se = [(k, w) for k in keywords for w in windows(months, step=3)]
    for q, tag, (a, b) in jobs:
        try:
            for it in hn_window(f, q, tag, a, b):
                seen.setdefault(it["url"], it)
        except Exception as e:
            warn(f"hn {tag} {q!r} {a}: {type(e).__name__}")
    for k, (a, b) in jobs_se:
        try:
            for it in se_window(f, k, a, b):
                seen.setdefault(it["url"], it)
        except Exception as e:
            warn(f"softwarerecs {k!r} {a}: {type(e).__name__}")
    items = sorted(seen.values(), key=lambda x: x["date"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(i) + "\n" for i in items))
    return items


def week_windows(weeks: int, today: date | None = None) -> list[tuple[int, int]]:
    """(start, end) epoch pairs for `weeks` complete Monday-to-Monday UTC weeks, oldest first."""
    t = today or date.today()
    monday = date.fromordinal(t.toordinal() - t.weekday())
    edges = [int(datetime.combine(date.fromordinal(monday.toordinal() - 7 * i), datetime.min.time(),
                                  timezone.utc).timestamp()) for i in range(weeks, -1, -1)]
    return list(zip(edges, edges[1:]))


def hn_weekly_counts(f: Fetcher, term: str, weeks: int = 104) -> list[int]:
    """HN comments per week containing `term` (Algolia's exact nbHits; no posts are fetched)."""
    out = []
    for a, b in week_windows(weeks):
        d = f.get_json(_HN, params={"query": f'"{term}"', "tags": "comment", "hitsPerPage": 0,
                                    "numericFilters": f"created_at_i>={a},created_at_i<{b}"})
        out.append(int(d["nbHits"]))
    return out
