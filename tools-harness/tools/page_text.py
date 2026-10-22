"""
Read the WHOLE page in the user's front window, not just the visible part, from the accessibility
tree: no scrolling, no OCR, no scripting setup. Collapsed sections (spec accordions, "Product
information", FAQ items) are pressed open first. `query` returns only the passages that mention it.
"""
from __future__ import annotations

from tools.ax_context import page_text
from tools.screen_act import _guard

READ_PAGE_TEXT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_page_text",
        "description": (
            "Reads the full text of the page or window the user has open (the entire page, including parts "
            "scrolled out of view and collapsed sections such as spec lists, 'Product information' or FAQ "
            "accordions, which it opens first). One call, no scrolling. Use it for ANY question about what a "
            "page says: specs, price, reviews, an article, a table, 'what does it say about X'. Pass `query` "
            "(a word or phrase, e.g. 'battery' or 'weight') to get only the passages that mention it, which "
            "is faster and cheaper; omit it to read the page from the top. Prefer it over scrolling and "
            "get_screen_context for reading; use get_screen_context only for what is visibly on screen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to look for; returns matching passages only. Omit to read the page."},
                "app": {"type": "string", "description": "App to read, e.g. 'Google Chrome'; omit for the front window."},
                "expand": {"type": "boolean", "default": True, "description": "Open collapsed sections first."},
            },
        },
    },
}

_MAX_CHARS = 12000
_MAX_HITS = 40


def passages(lines: list[str], query: str) -> list[str]:
    """Lines mentioning any query word, each with one neighbour line of context, merged, in page order."""
    words = [w for w in query.lower().replace(",", " ").split() if len(w) > 1]
    if not words:
        return lines
    keep: set[int] = set()
    hits = 0
    for i, ln in enumerate(lines):
        low = ln.lower()
        if any(w in low for w in words):
            keep.update(j for j in (i - 1, i, i + 1) if 0 <= j < len(lines))
            hits += 1
            if hits >= _MAX_HITS:
                break
    return [lines[i] for i in sorted(keep)]


def read_page_text(query: str = "", app: str = "", expand: bool = True) -> str:
    if (why := _guard()):
        return why
    try:
        title, url, lines, opened = page_text(app, expand)
    except Exception as e:
        return f"Could not read the page: {e}"
    if not lines:
        return "No readable page text (is a web page in front, and Accessibility allowed?)."
    total = sum(len(x) for x in lines)
    head = f"Page: {title}\n" + (f"URL: {url}\n" if url else "") + f"({len(lines)} lines, {total} chars" + (f"; opened {opened} collapsed section(s)" if opened else "") + ")\n"
    picked = passages(lines, query) if query else lines
    if query and not picked:
        return head + f"\nNo passage mentions {query!r}."
    body = "\n".join(picked)
    if len(body) > _MAX_CHARS:
        body = body[:_MAX_CHARS] + f"\n... [cut at {_MAX_CHARS} of {len(body)} chars: pass a query to narrow]"
    return head + "\n" + body
