"""
Typed result object for all search backends.

Replaces raw string returns so call sites can distinguish
good results, empty results, and errors without pattern-matching strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


class SearchSnippet(BaseModel):
    """One structured search result item from any backend."""
    title: str = ""
    url: str = ""
    snippet: str = ""
    published_date: str = ""
    source: str = ""


@dataclass
class SearchResult:
    content: str          # text to show the model (empty string if not ok)
    ok: bool              # True = usable result, False = error or invalid
    error: str = ""       # human-readable reason when not ok
    source: str = ""      # which backend produced this ("tavily", "serpapi", etc.)
    query: str = ""       # the query that was run
    items: list[SearchSnippet] = field(default_factory=list)  # structured snippets for reranking/synthesis

    def __post_init__(self) -> None:
        # Coerce any raw dicts (from backends that haven't been updated yet)
        # into SearchSnippet so the rest of the pipeline always sees typed objects.
        self.items = [
            SearchSnippet.model_validate(item) if isinstance(item, dict) else item
            for item in (self.items or [])
        ]

    def to_model_str(self) -> str:
        """
        Return the string the model should see.
        If ok, returns content.
        If not ok, returns a structured sentinel the system prompt instructs
        the model to surface verbatim — never a raw exception or foreign-language text.
        """
        if self.ok:
            return self.content
        reason = self.error or "no results"
        return f"[search_failed source={self.source} reason={reason!r}]"
