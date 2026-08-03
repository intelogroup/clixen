"""
PubMed search backend — NCBI E-utilities (no API key required).

Flow:
  esearch  → PMIDs (JSON)
  efetch   → article XML (title, abstract, authors, journal, date, DOI)
  unpaywall → open-access PDF link per DOI (requires UNPAYWALL_EMAIL)

Rate limits:
  3 req/s without key  |  10 req/s with NCBI_API_KEY env var.
  Both esearch and efetch count as separate requests.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import requests

from tools.search_result import SearchResult, SearchSnippet
from tools.unpaywall import get_oa_url

log = logging.getLogger("pubmed")

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_TIMEOUT = 12.0
_MAX_ABSTRACT_CHARS = 600

# NCBI rate limit: 3 req/s without key, 10 req/s with key.
# Each execute() call makes 2 requests (esearch + efetch), so enforce a minimum
# 400ms gap between any two NCBI HTTP calls to stay safely under the limit.
_ncbi_lock = threading.Lock()
_ncbi_last_call: float = 0.0
_NCBI_MIN_INTERVAL = 0.4  # seconds


def _ncbi_get(url: str, params: dict) -> requests.Response:
    """Rate-limited GET for all NCBI endpoints."""
    global _ncbi_last_call
    with _ncbi_lock:
        elapsed = time.monotonic() - _ncbi_last_call
        if elapsed < _NCBI_MIN_INTERVAL:
            time.sleep(_NCBI_MIN_INTERVAL - elapsed)
        resp = requests.get(url, params=params, timeout=_TIMEOUT)
        _ncbi_last_call = time.monotonic()
    return resp


def _ncbi_params(extra: dict) -> dict:
    """Merge optional NCBI_API_KEY into request params."""
    p = {"tool": "clixen-search", "email": os.environ.get("UNPAYWALL_EMAIL", "clixen@localhost")}
    key = os.environ.get("NCBI_API_KEY", "")
    if key:
        p["api_key"] = key
    p.update(extra)
    return p


def _esearch(query: str, max_results: int, reldate: int | None = None,
             sort: str = "relevance") -> list[str]:
    """Return list of PMIDs for the query.

    reldate: restrict to articles with a PubMed date within the last N days
    (NCBI's own reldate/datetype params — a real filter, unlike embedding
    "last N days"[dp] as free text inside `term`, which esearch does not
    reliably honor for recency).
    """
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
        "sort": sort,
    }
    if reldate is not None:
        params["reldate"] = reldate
        params["datetype"] = "pdat"
    resp = _ncbi_get(f"{_EUTILS}/esearch.fcgi", params=_ncbi_params(params))
    resp.raise_for_status()
    return resp.json().get("esearchresult", {}).get("idlist", [])


def _efetch_xml(pmids: list[str]) -> ET.Element:
    """Fetch PubMed XML for a list of PMIDs."""
    resp = _ncbi_get(
        f"{_EUTILS}/efetch.fcgi",
        params=_ncbi_params({
            "db": "pubmed",
            "id": ",".join(pmids),
            "rettype": "abstract",
            "retmode": "xml",
        }),
    )
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def _parse_articles(root: ET.Element) -> list[dict]:
    """Parse PubMed XML into a list of article dicts."""
    articles = []
    for article in root.iter("PubmedArticle"):
        mc = article.find("MedlineCitation")
        if mc is None:
            continue

        art = mc.find("Article")
        if art is None:
            continue

        # Title
        title_el = art.find("ArticleTitle")
        title = (title_el.text or "").strip() if title_el is not None else ""

        # Abstract — may have multiple <AbstractText> with Label attr
        abstract_parts = []
        for ab in art.iter("AbstractText"):
            label = ab.get("Label", "")
            text = (ab.text or "").strip()
            if text:
                abstract_parts.append(f"{label}: {text}" if label else text)
        abstract = " ".join(abstract_parts)
        if len(abstract) > _MAX_ABSTRACT_CHARS:
            abstract = abstract[:_MAX_ABSTRACT_CHARS].rsplit(" ", 1)[0] + "…"

        # Journal + year
        journal_el = art.find("Journal/Title")
        journal = (journal_el.text or "").strip() if journal_el is not None else ""
        year_el = mc.find("Article/Journal/JournalIssue/PubDate/Year")
        year = (year_el.text or "").strip() if year_el is not None else ""

        # Authors
        authors = []
        for auth in art.findall("AuthorList/Author"):
            ln = auth.findtext("LastName", "")
            ini = auth.findtext("Initials", "")
            if ln:
                authors.append(f"{ln} {ini}".strip())
        author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

        # DOI and PMID from PubmedData
        doi = ""
        pmid = mc.findtext("PMID", "")
        for art_id in article.iter("ArticleId"):
            if art_id.get("IdType") == "doi":
                doi = (art_id.text or "").strip()
            if art_id.get("IdType") == "pubmed" and not pmid:
                pmid = (art_id.text or "").strip()

        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""

        articles.append({
            "title": title,
            "abstract": abstract,
            "journal": journal,
            "year": year,
            "authors": author_str,
            "doi": doi,
            "pmid": pmid,
            "url": url,
        })
    return articles


def execute(query: str, max_results: int = 5, reldate: int | None = None,
            sort: str = "relevance") -> SearchResult:
    try:
        def _search():
            pmids = _esearch(query, max_results, reldate=reldate, sort=sort)
            if not pmids:
                return []
            root = _efetch_xml(pmids)
            return _parse_articles(root)

        _ex = ThreadPoolExecutor(max_workers=1)
        _f = _ex.submit(_search)
        try:
            articles = _f.result(timeout=_TIMEOUT + 5)
        except FuturesTimeout:
            _ex.shutdown(wait=False, cancel_futures=True)
            return SearchResult(content="", ok=False, error="timeout", source="pubmed", query=query)
        except Exception as exc:
            _ex.shutdown(wait=False, cancel_futures=True)
            return SearchResult(content="", ok=False, error=str(exc), source="pubmed", query=query)
        _ex.shutdown(wait=False)
    except Exception as exc:
        return SearchResult(content="", ok=False, error=str(exc), source="pubmed", query=query)

    if not articles:
        return SearchResult(content="", ok=False, error="no results", source="pubmed", query=query)

    parts = []
    items = []
    for a in articles:
        oa_url = get_oa_url(a["doi"]) if a["doi"] else None
        full_text_note = f" [full text: {oa_url}]" if oa_url else ""
        date_label = f" ({a['year']})" if a["year"] else ""
        journal_label = f" — {a['journal']}" if a["journal"] else ""
        authors_label = f"\n  Authors: {a['authors']}" if a["authors"] else ""
        snippet = a["abstract"] or a["title"]

        parts.append(
            f"- {a['title']}{date_label}{journal_label}{full_text_note} ({a['url']})"
            f"{authors_label}"
            f"\n  {snippet}"
        )
        items.append(SearchSnippet(
            title=a["title"],
            url=oa_url or a["url"],
            snippet=snippet,
            published_date=a["year"],
            source="pubmed",
        ))

    return SearchResult(
        content="\n\n".join(parts),
        ok=True,
        source="pubmed",
        query=query,
        items=items,
    )
