"""
arXiv search backend — arXiv public API (no API key required).

API: http://export.arxiv.org/api/query
Returns Atom XML; parsed with stdlib xml.etree.ElementTree.

Rate limit: ~3 req/s. No key needed.
Best for: preprints in CS, physics, math, quantitative biology, statistics, economics.
"""
from __future__ import annotations

import logging
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import requests

from tools.search_result import SearchResult, SearchSnippet

log = logging.getLogger("arxiv")

_API = "http://export.arxiv.org/api/query"
_TIMEOUT = 12.0
_ATOM = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"
_MAX_ABSTRACT_CHARS = 600

_NS = {
    "atom": _ATOM,
    "arxiv": _ARXIV_NS,
}


def _trim(text: str, max_chars: int = _MAX_ABSTRACT_CHARS) -> str:
    text = " ".join(text.split())  # normalise whitespace
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


def execute(query: str, max_results: int = 5) -> SearchResult:
    def _call():
        resp = requests.get(
            _API,
            params={
                "search_query": f"all:{urllib.parse.quote_plus(query)}",
                "max_results": min(max_results, 10),
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return ET.fromstring(resp.content)

    _ex = ThreadPoolExecutor(max_workers=1)
    _f = _ex.submit(_call)
    try:
        root = _f.result(timeout=_TIMEOUT + 3)
    except FuturesTimeout:
        _ex.shutdown(wait=False, cancel_futures=True)
        return SearchResult(content="", ok=False, error="timeout", source="arxiv", query=query)
    except Exception as exc:
        _ex.shutdown(wait=False, cancel_futures=True)
        return SearchResult(content="", ok=False, error=str(exc), source="arxiv", query=query)
    _ex.shutdown(wait=False)

    entries = root.findall("atom:entry", _NS)
    if not entries:
        return SearchResult(content="", ok=False, error="no results", source="arxiv", query=query)

    parts = []
    items = []
    for entry in entries:
        title = (entry.findtext("atom:title", "", _NS) or "").strip().replace("\n", " ")
        abstract = _trim(entry.findtext("atom:summary", "", _NS) or "")
        published = (entry.findtext("atom:published", "", _NS) or "")[:10]

        # Prefer the abs link (HTML landing page) over PDF directly
        url = ""
        pdf_url = ""
        for link in entry.findall("atom:link", _NS):
            rel = link.get("rel", "")
            href = link.get("href", "")
            if link.get("type") == "text/html" or rel == "alternate":
                url = href
            if link.get("title") == "pdf":
                pdf_url = href
        if not url:
            url = pdf_url

        # Authors
        authors = [
            (a.findtext("atom:name", "", _NS) or "").strip()
            for a in entry.findall("atom:author", _NS)
        ]
        author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

        # arXiv categories
        cats = [c.get("term", "") for c in entry.findall("arxiv:primary_category", _NS)]
        cat_label = f" [{cats[0]}]" if cats else ""

        date_label = f" ({published})" if published else ""
        pdf_note = f" [PDF: {pdf_url}]" if pdf_url and pdf_url != url else ""

        parts.append(
            f"- {title}{date_label}{cat_label}{pdf_note} ({url})"
            f"\n  Authors: {author_str}"
            f"\n  {abstract}"
        )
        items.append(SearchSnippet(
            title=title,
            url=url or pdf_url,
            snippet=abstract,
            published_date=published,
            source="arxiv",
        ))

    return SearchResult(
        content="\n\n".join(parts),
        ok=True,
        source="arxiv",
        query=query,
        items=items,
    )
