"""
Local knowledge search tool — searches all registered DBs on this machine.
Gives the model access to zl_master_board, malaria_thesis, qwen demo,
and clixen's own KB without hitting any external API.
"""

SCHEMA = {
    "type": "function",
    "function": {
        "name": "local_search",
        "description": (
            "Search the local knowledge base — includes past web searches, "
            "OCR'd documents, audio transcripts, ZL hospital docs (French/English), "
            "malaria thesis research, and library docs. "
            "Use this BEFORE web_search to avoid unnecessary API calls. "
            "Good for: documents already processed, research already done, "
            "anything that might have been seen before."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for"
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: filter to specific DBs. "
                        "Options: clixen, zl_master_board_en, zl_master_board_fr, "
                        "malaria_thesis_en, malaria_thesis_fr, qwen_lancedb_demo. "
                        "Omit to search all."
                    )
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default 4)",
                    "default": 4
                }
            },
            "required": ["query"]
        }
    }
}


def execute(query: str, sources: list = None, top_k: int = 4) -> str:
    from store.db_discovery import search_all

    results = search_all(query, top_k=top_k, dbs=sources or None)

    if not results:
        return "No relevant results found in local knowledge base."

    parts = []
    for r in results:
        owner = r.get("owner", r.get("db", ""))
        text  = r.get("text") or r.get("content", "")
        src   = r.get("source", "")
        parts.append(f"[{owner}] {src}\n{text[:300]}")

    return "\n\n---\n\n".join(parts)
