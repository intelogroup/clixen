"""
arXiv search tool — standalone LLM-callable wrapper around arxiv_search.py.

Usage:
    from tools.arxiv_tool import execute as search_arxiv
    result = search_arxiv("transformer architecture")

Wraps arxiv_search.execute() and returns a plain-text summary for the LLM.
"""

from __future__ import annotations

import logging

from tools.arxiv_search import execute as _arxiv_execute
from tools.search_result import SearchResult

log = logging.getLogger("arxiv_tool")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_arxiv",
        "description": (
            "Search arXiv for academic papers by keyword, topic, or title. "
            "Returns paper title, authors, abstract, publication date, categories, and PDF links. "
            "Best for: computer science, physics, math, quantitative biology, statistics, economics preprints. "
            "Use this when the user wants to find recent research papers, check what's published on a topic, "
            "or get scientific answers. Rate limit: ~3 requests/second."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — keywords, topic, title fragment, or author name",
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
    """Search arXiv for papers and return a plain-text summary."""
    try:
        result: SearchResult = _arxiv_execute(query=query, max_results=max_results)

        if not result.ok:
            return f"[arxiv] {result.error or 'no results for query: ' + query}"

        return result.content

    except Exception as e:
        log.error("arxiv search failed: %s", e, exc_info=True)
        return f"[arxiv] search failed: {e}"
