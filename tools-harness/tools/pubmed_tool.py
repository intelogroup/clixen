"""
PubMed search tool — standalone LLM-callable wrapper around pubmed.py.

Usage:
    from tools.pubmed_tool import execute as search_pubmed
    result = search_pubmed("diabetes treatment 2025")

Wraps pubmed.execute() and returns a plain-text summary for the LLM.
"""

from __future__ import annotations

import logging

from tools.pubmed import execute as _pubmed_execute
from tools.search_result import SearchResult

log = logging.getLogger("pubmed_tool")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_pubmed",
        "description": (
            "Search PubMed for biomedical and life sciences literature. "
            "Returns PMID, title, authors, journal, publication year, abstract, DOI, and open-access links. "
            "Best for: medical research, clinical trials, drug studies, disease information, public health. "
            "Use this when the user asks about health, medicine, diseases, treatments, clinical research, "
            "or medical literature. Rate limit: ~3 requests/second (free tier)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — disease, drug, gene, treatment, or MeSH terms",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max number of results (1-10, default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}


def execute(query: str, max_results: int = 5) -> str:
    """Search PubMed for articles and return a plain-text summary."""
    try:
        result: SearchResult = _pubmed_execute(query=query, max_results=max_results)

        if not result.ok:
            return f"[pubmed] {result.error or 'no results for query: ' + query}"

        return result.content

    except Exception as e:
        log.error("pubmed search failed: %s", e, exc_info=True)
        return f"[pubmed] search failed: {e}"
