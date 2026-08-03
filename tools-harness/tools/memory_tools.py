"""
Persistent cross-session memory — explicit remember/forget tools + recall hook.

Unlike store/conversation.py (per-chat sliding window, ephemeral), this survives across
sessions and transports. Single global namespace: this is a 1:1 personal assistant.

Storage reuses store/knowledge_base.py (local nomic-embed + LanceDB semantic search) on a
DEDICATED db path so the disposable search-result cache never touches precious memories.
Memories are stored with source="user_memory", which TTL maps to infinity in knowledge_base.
"""
import logging
from pathlib import Path

from store.knowledge_base import KnowledgeBase, _content_id

log = logging.getLogger(__name__)

_SOURCE = "user_memory"
_SESSION_SOURCE = "session_memory"
_MEM_DB_PATH = str(Path(__file__).parent.parent / "data" / "memory.lance")

# Cosine-distance gates (nomic-embed). Measured: relevant matches land ~0.73-0.84,
# unrelated noise >1.0. Recall keeps anything plausibly on-topic; forget is destructive
# so it only acts on near-exact topical matches.
# ponytail: thresholds tuned on a small sample — widen/narrow if recall misses or leaks.
# Distance cutoffs per embed backend — OpenAI text-embedding-3-small and
# Ollama nomic-embed produce different vector distributions (verified: correct
# matches land ~0.73-1.24 openai vs ~0.75-0.77 nomic; unrelated ~1.39+ openai).
_RECALL_DIST = {"openai": 1.3, "ollama": 0.88}
_FORGET_DIST = {"openai": 1.0, "ollama": 0.80}


def _recall_threshold() -> float:
    from store import knowledge_base as _kbmod
    return _RECALL_DIST.get(_kbmod.EMBED_BACKEND, 0.88)


def _forget_threshold() -> float:
    from store import knowledge_base as _kbmod
    return _FORGET_DIST.get(_kbmod.EMBED_BACKEND, 0.80)

_mem_kb: KnowledgeBase | None = None


def _kb() -> KnowledgeBase:
    """Lazy singleton — connect to the memory DB once, reuse across calls."""
    global _mem_kb
    if _mem_kb is None:
        _mem_kb = KnowledgeBase(db_path=_MEM_DB_PATH)
    return _mem_kb


def remember(fact: str) -> str:
    """Store a durable fact about the user. Returns a short confirmation."""
    fact = (fact or "").strip()
    if not fact:
        return "Nothing to remember — empty fact."
    try:
        kb = _kb()
        # Idempotent: drop any prior identical fact (same content → same id) before re-adding.
        kb.table.delete(f"id = '{_content_id(fact)}'")
        kb.store(content=fact, source=_SOURCE, method="manual")
        return f"Got it — I'll remember that: {fact}"
    except Exception as e:  # Ollama/embed down — don't crash the turn
        log.warning("remember failed: %s", e)
        return f"[error] couldn't save memory: {e}"


def forget(query: str) -> str:
    """Delete the single best-matching memory. Destructive → conservative by design:
    removes only the closest match (within threshold), never a whole neighborhood of
    related facts. Call again to remove more."""
    query = (query or "").strip()
    if not query:
        return "Nothing to forget — empty query."
    try:
        hits = _kb().search(query, top_k=1, source_filter=_SOURCE)
        if not hits or hits[0].get("_distance", 99) > _forget_threshold():
            return f"No memory found matching: {query}"
        top = hits[0]
        _kb().table.delete(f"id = '{top['id']}'")
        return f"Forgot: {top['content']}"
    except Exception as e:
        log.warning("forget failed: %s", e)
        return f"[error] couldn't forget: {e}"


def recall_block(query: str) -> str:
    """
    Harness hook: return a system-prompt block of memories relevant to `query`,
    or "" when there's nothing to recall (empty namespace) or recall fails.
    Two embeds + two LanceDB lookups (user_memory + session fold summaries);
    safe to call on every turn.
    """
    try:
        hits = _kb().search(query or "", top_k=5, source_filter=_SOURCE)
        hits += _kb().search(query or "", top_k=5, source_filter=_SESSION_SOURCE)
    except Exception as e:
        log.debug("recall skipped: %s", e)
        return ""
    # Only inject on-topic memories — otherwise every turn floods the prompt with all of them.
    hits = [h for h in hits if h.get("_distance", 99) <= _recall_threshold()]
    if not hits:
        return ""
    bullets = []
    for h in hits:
        if h.get("source") == _SESSION_SOURCE:
            # Fold summaries are per-chat context ("earlier conversation with X") —
            # label the source so the model doesn't misattribute them as user-stated facts.
            cid = (h.get("query", "") or "").replace("fold-summary:", "").strip() or "past conversation"
            bullets.append(f"- [earlier conversation {cid}: {h['content']}]")
        else:
            bullets.append(f"- {h['content']}")
    return f"## What you remember\n{chr(10).join(bullets)}\n"


def mem_block_for(query: str) -> str:
    """Specialist hook: same recall as recall_block, zero logging/noise. Returns "" if empty."""
    if not (query or "").strip():
        return ""
    try:
        return recall_block(query)
    except Exception:
        return ""


def save_session_summary(chat_id: str, summary: str) -> None:
    """Save a conversation fold summary to persistent session memory (best-effort).
    Overwrites previous entry for same chat_id — no stale accumulation."""
    try:
        kb = _kb()
        key = f"fold-summary:{chat_id}"
        kb.table.delete(f"query = '{key}'")
        kb.store(content=summary, source=_SESSION_SOURCE, query=key, method="auto_fold")
    except Exception:
        pass


def search_sessions(query: str, top_k: int = 5) -> str:
    """Search past session summaries AND remembered facts. Returns formatted results
    or empty string.

    Originally only searched session-fold summaries (_SESSION_SOURCE) — remembered
    facts (_SOURCE, written by `remember()`) are normally auto-injected via
    `recall_block()` every turn, but the orchestrator's own tool-calling loop had no
    way to explicitly re-query them (confirmed live 2026-07-13: this was the only
    memory-search tool the model reached for, and it came back empty for a fact that
    was actually stored, just under the other source)."""
    try:
        hits = _kb().search(query, top_k=top_k, source_filter=_SESSION_SOURCE)
        hits += _kb().search(query, top_k=top_k, source_filter=_SOURCE)
        hits = [h for h in hits if h.get("_distance", 99) <= _recall_threshold()]
    except Exception as e:
        log.warning("search_sessions failed: %s", e)
        return "[error] search_sessions failed"
    if not hits:
        return ""
    out = []
    for h in hits:
        if h.get("query", "").startswith("fold-summary:"):
            cid = h["query"].replace("fold-summary:", "")
            out.append(f"Previous session ({cid}):\n{h['content']}")
        else:
            out.append(f"Remembered fact:\n{h['content']}")
    return "\n\n".join(out)


MEMORY_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Save a durable fact about the user that should persist across all future "
                "conversations (preferences, names, projects, recurring details). Use when the "
                "user says 'remember that...', 'keep in mind...', or states a lasting preference."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "The single fact to remember, as a concise statement.",
                    }
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            "description": (
                "Delete previously remembered facts that match a description. Use when the user "
                "says 'forget that...', 'that's no longer true', or asks to remove a memory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Description of the memory to remove.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_sessions",
            "description": (
                "Search past conversation sessions (WhatsApp, voice, Telegram) for relevant "
                "information. Use when you need to recall something from an earlier session. "
                "Returns relevant session summaries. NOT called automatically — invoke explicitly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for in past sessions.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results (1-10, default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

MEMORY_EXECUTORS = {
    "remember": lambda args: remember(args.get("fact", "")),
    "forget": lambda args: forget(args.get("query", "")),
    "search_sessions": lambda args: search_sessions(args.get("query", ""), top_k=args.get("top_k", 5)),
}
