"""Newest-citation lookup for a Wikipedia article: parse the dates, don't guess.

Why this exists. Asked to "find the newest source cited", the orb read the
references with `ask_research_agent` and answered from prose. That took 75 of a
178 s task and rested on the model eyeballing a list — the single weakest step in
the chain, and unverifiable afterwards.

The references are structured data on the page: each `<li id="cite_note-…">` is one
citation, with its own date. So this parses them, extracts a date per citation,
sorts by it, and returns the newest first — with the date it read, so the claim is
checkable. Citations whose date cannot be parsed are not ranked and are counted,
never silently dropped or guessed at.
"""

from __future__ import annotations

import html
import re
import urllib.parse

_MONTHS = {
    m: i for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"], start=1
    )
}

# Ordered by precision; the first pattern that matches a citation wins.
_DATE_PATTERNS = (
    (re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b"), "ymd"),        # 2025-06-14
    (re.compile(r"\b(\d{1,2}) ([A-Z][a-z]+) ((?:19|20)\d{2})\b"), "dmy"),  # 14 June 2025
    (re.compile(r"\b([A-Z][a-z]+) (\d{1,2}),? ((?:19|20)\d{2})\b"), "mdy"),  # June 14, 2025
    (re.compile(r"\b([A-Z][a-z]+) ((?:19|20)\d{2})\b"), "my"),           # June 2025
    (re.compile(r"\b((?:19|20)\d{2})\b"), "y"),                          # 2025
)

REF_LI = re.compile(r'<li id="cite(?:&#95;|_)note[^"]*"[^>]*>(.*?)</li>', re.S)
URL_RE = re.compile(r'href="(https?://[^"]+)"')
TAG_RE = re.compile(r"<[^>]+>")
# MediaWiki inlines per-template CSS inside the reference text; without this the
# "citation" reads as a wall of .mw-parser-output rules and the date parser finds
# a date in the CSS instead of in the citation.
STYLE_RE = re.compile(r"<(style|script)\b.*?</\1>", re.S | re.I)
BACKLINK_RE = re.compile(r'<span class="mw-cite-backlink">.*?</span>', re.S)
# "Retrieved June 23, 2026" is when Wikipedia's editor opened the page, not when
# the source was published. Ranking on it put a 2025 CNN article below two
# templates whose only "date" was the day someone re-checked the link — measured
# 2026-10-25, which is exactly the mistake this tool exists to stop.
ACCESSDATE_RE = re.compile(r'<span class="reference-accessdate">.*?</span>', re.S)


def _clean(chunk: str) -> str:
    text = STYLE_RE.sub(" ", chunk)
    text = BACKLINK_RE.sub(" ", text)  # the "^" jump-back arrow, not content
    text = ACCESSDATE_RE.sub(" ", text)  # retrieval date: not a publication date
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", text))).strip()


def _access_date(chunk: str) -> str:
    """The citation's 'Retrieved …' text, kept for display but never ranked on."""
    m = ACCESSDATE_RE.search(chunk)
    if not m:
        return ""
    _, label = _date_of(_clean(m.group(0)))
    return label


def _month(name: str) -> int:
    return _MONTHS.get(name.lower(), 0)


def _date_of(text: str) -> tuple[int, str]:
    """Best (sortable key, label) for a citation. 0 means "no parseable date"."""
    for pattern, kind in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        g = m.groups()
        if kind == "ymd":
            y, mo, d = int(g[0]), int(g[1]), int(g[2])
        elif kind == "dmy":
            d, mo, y = int(g[0]), _month(g[1]), int(g[2])
        elif kind == "mdy":
            mo, d, y = _month(g[0]), int(g[1]), int(g[2])
        elif kind == "my":
            mo, y = _month(g[0]), int(g[1])
            d = 0
        else:
            y, mo, d = int(g[0]), 0, 0
        if not y or (kind in ("ymd", "dmy", "mdy") and not (mo and d)):
            continue
        if kind in ("ymd", "dmy", "mdy"):
            return y * 10000 + mo * 100 + d, f"{y:04d}-{mo:02d}-{d:02d}"
        if kind == "my":
            return y * 10000 + mo * 100, f"{y:04d}-{mo:02d}"
        return y * 10000, f"{y:04d}"
    return 0, ""


def wikipedia_references(article: str, limit: int = 8) -> str:
    """Return the newest dated citations of a Wikipedia article, newest first."""
    title = (article or "").strip()
    if not title:
        return "[error] wikipedia_references needs an article title or URL."
    if "wikipedia.org/wiki/" in title:
        title = urllib.parse.unquote(title.split("/wiki/", 1)[1])
    title = title.replace("_", " ")

    api = (
        "https://en.wikipedia.org/w/api.php?action=parse&format=json&formatversion=2"
        "&redirects=1&prop=text&page=" + urllib.parse.quote(title)
    )
    try:
        import requests

        r = requests.get(api, timeout=20, headers={"User-Agent": "clixen-orb/1.0"})
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        return f"[error] could not reach Wikipedia for '{title}': {exc}"
    parsed = data.get("parse") or {}
    page_html = parsed.get("text") or ""
    resolved = parsed.get("title") or title
    if not page_html:
        return f"[error] Wikipedia returned no content for '{title}' (missing page or bad title)."

    rows, undated = [], 0
    for chunk in REF_LI.findall(page_html):
        text = _clean(chunk)
        if len(text) < 12:
            continue
        key, label = _date_of(text)
        if not key:
            undated += 1
            continue
        urls = URL_RE.findall(chunk)
        rows.append({"key": key, "date": label, "text": text[:220], "url": urls[0] if urls else ""})

    if not rows:
        return (f"[error] no dated citations found in '{resolved}' "
                f"({undated} undated reference(s) — the article may cite undated sources only).")

    rows.sort(key=lambda row: row["key"], reverse=True)
    lines = [f"Newest dated citations in Wikipedia's '{resolved}' (parsed from the reference list):"]
    for i, row in enumerate(rows[: max(1, min(int(limit or 8), 20))], 1):
        link = f" — {row['url']}" if row["url"] else " — (no external link in this citation)"
        lines.append(f"{i}. [{row['date']}] {row['text']}{link}")
    if undated:
        lines.append(f"({undated} further citation(s) carry no parseable date and are not ranked here.)")
    lines.append(
        "Each date is read from that citation's own text. If the newest one looks like an "
        "access/retrieval date rather than a publication date, take the next."
    )

    from tools.injection_guard import wrap_external_output

    return wrap_external_output("wikipedia_references", "\n".join(lines))


WIKIPEDIA_REFERENCES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "wikipedia_references",
        "description": (
            "The newest dated sources cited by a Wikipedia article, newest first, with each "
            "citation's own date. Use this instead of reading the reference list yourself "
            "when asked for the latest/newest source."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "article": {"type": "string", "description": "Article title or full Wikipedia URL"},
                "limit": {"type": "integer", "description": "How many to return (default 8, max 20)"},
            },
            "required": ["article"],
        },
    },
}
