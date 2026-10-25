"""
Persistent cross-session memory — explicit remember/forget tools + recall hook.

Unlike store/conversation.py (per-chat sliding window, ephemeral), this survives across
sessions and transports. Single global namespace: this is a 1:1 personal assistant.

Storage reuses store/knowledge_base.py (local nomic-embed + LanceDB semantic search) on a
DEDICATED db path so the disposable search-result cache never touches precious memories.
Memories are stored with source="user_memory", which TTL maps to infinity in knowledge_base.
"""
import logging
import threading

from store.knowledge_base import KnowledgeBase, _content_id

log = logging.getLogger(__name__)

_SOURCE = "user_memory"
_SESSION_SOURCE = "session_memory"
_MEM_DB_PATH = None  # resolved in _memory_kb(); see tools/vault.py


def _memory_db_path() -> str:
    global _MEM_DB_PATH
    if _MEM_DB_PATH is None:
        from tools.vault_paths import db_path as _vault_db_path, ensure_data_dir, migrate_legacy_data
        ensure_data_dir()
        migrate_legacy_data()
        _MEM_DB_PATH = _vault_db_path("memory.lance")
    return _MEM_DB_PATH

# Cosine-distance gates (nomic-embed). Measured: relevant matches land ~0.73-0.84,
# unrelated noise >1.0. Recall keeps anything plausibly on-topic; forget is destructive
# so it only acts on near-exact topical matches.
# ponytail: thresholds tuned on a small sample — widen/narrow if recall misses or leaks.
# Distance cutoffs per embed backend — OpenAI text-embedding-3-small and
# Ollama nomic-embed produce different vector distributions (verified: correct
# matches land ~0.73-1.24 openai vs ~0.75-0.77 nomic; unrelated ~1.39+ openai).
_RECALL_DIST = {"openai": 1.3, "ollama": 0.88}
_FORGET_DIST = {"openai": 1.0, "ollama": 0.80}


def _memory_backend() -> str:
    """The backend the memory store's vectors were built with.

    Read off the store, not knowledge_base.EMBED_BACKEND: that global reflects whichever
    embed ran last anywhere in the process and still holds its "ollama" default before
    the first one, so a fresh process gated OpenAI distances (which run to ~1.3) against
    the nomic cutoff of 0.88 and discarded every genuine match.
    """
    from store import knowledge_base as _kbmod
    return _kb().embed_backend or _kbmod.EMBED_BACKEND


def _recall_threshold() -> float:
    return _RECALL_DIST.get(_memory_backend(), 0.88)


def _forget_threshold() -> float:
    return _FORGET_DIST.get(_memory_backend(), 0.80)

_mem_kb: KnowledgeBase | None = None
_session_summary_lock = threading.RLock()


def _kb() -> KnowledgeBase:
    """Lazy singleton — connect to the memory DB once, reuse across calls."""
    global _mem_kb
    if _mem_kb is None:
        _mem_kb = KnowledgeBase(db_path=_memory_db_path())
    return _mem_kb


_ENTITY_PREFIX = "entity:"
_TIER_PREFIX = "tier:"
TIERS = ("always", "on_relevance", "surface_only")  # LifeOS load_timing axis; default is on_relevance (existing similarity-gated behavior)


def _build_tags(entity_type: str = "", entity_name: str = "", tier: str = "") -> str:
    """Pack entity + tier metadata into the query field as ';'-separated tags —
    mirrors the fold-summary: prefix convention below, not a schema change: KnowledgeBase
    drops+recreates its table on any SCHEMA field mismatch, which would wipe all stored
    memories, so new columns are off the table."""
    parts = []
    if entity_name:
        parts.append(f"{_ENTITY_PREFIX}{entity_type}:{entity_name}")
    if tier and tier in TIERS and tier != "on_relevance":  # on_relevance is the implicit default, no tag needed
        parts.append(f"{_TIER_PREFIX}{tier}")
    return ";".join(parts)


def _parse_tags(query: str) -> dict:
    tags: dict[str, str] = {}
    for part in (query or "").split(";"):
        key, sep, val = part.partition(":")
        if sep:
            tags[key.strip()] = val.strip()
    return tags


# ── Memory v2: version, don't overwrite ──────────────────────────────────────
# Ported from the cloud repo's ADR 0018, adapted to a fixed schema. KnowledgeBase
# drops and recreates its table on any field mismatch — which would wipe every
# memory — so new columns are off the table and the metadata rides in the row's
# otherwise-unused `url` field as JSON.
#
# Why versioning matters here: `remember` is a tool the model can call, and a
# memory that it wrote is indistinguishable, later, from an instruction. The cloud
# design calls a memory write "a persistence primitive for a successful injection"
# and responds by (a) keeping history append-only instead of overwriting, and
# (b) treating memories as untrusted data when they re-enter the prompt. Both are
# cheap here and neither needs a schema change.
_LIVE = "live"
_MIN_CONFIDENCE = 0.4


def _meta(row: dict) -> dict:
    """Parse the JSON metadata stored in `url` (empty for legacy rows)."""
    raw = row.get("url") or ""
    if not raw.startswith("{"):
        return {}
    try:
        import json as _json

        loaded = _json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _is_live(row: dict) -> bool:
    meta = _meta(row)
    if meta.get("tombstoned"):
        return False
    if meta.get("valid_to"):
        return False
    return float(meta.get("confidence", 1.0)) >= _MIN_CONFIDENCE


def _set_meta(row_id: str, **changes) -> bool:
    """Merge `changes` into this row's metadata. Returns False when unavailable.

    A failed metadata write must never lose the memory itself: the row stays and
    the caller is told, rather than pretending the version was superseded.
    """
    import json as _json
    import time as _time

    try:
        rows = _kb().table.search().where(f"id = '{row_id}'", prefilter=True).limit(1).to_list()
    except Exception as e:
        log.warning("memory metadata read failed: %s", e)
        return False
    if not rows:
        return False
    meta = _meta(rows[0])
    meta.setdefault("valid_from", float(rows[0].get("created_at") or _time.time()))
    meta.update(changes)
    try:
        _kb().table.update(where=f"id = '{row_id}'", values={"url": _json.dumps(meta, sort_keys=True)})
        return True
    except Exception as e:
        log.warning("memory metadata write failed: %s", e)
        return False


def _new_meta(key: str = "", origin: str = "user_tool", confidence: float = 1.0) -> str:
    import json as _json
    import time as _time

    now = _time.time()
    return _json.dumps(
        {
            "key": key,
            "valid_from": now,
            "valid_to": 0.0,
            "confidence": round(float(confidence), 3),
            "last_confirmed_at": now,
            "origin": origin,
            "tombstoned": False,
        },
        sort_keys=True,
    )


def _supersede(key: str, new_id: str) -> int:
    """Close the live versions of `key`: they keep their history, stop being recall."""
    import time as _time

    if not key:
        return 0
    closed = 0
    for row in _memory_rows():
        if _meta(row).get("key") != key:
            continue
        if _set_meta(row["id"], valid_to=_time.time(), superseded_by=new_id):
            closed += 1
    return closed


def _memory_rows() -> list[dict]:
    """Every user_memory row (live AND superseded/tombstoned), one filtered scan.

    Named for what it returns: filtering to live versions is `_is_live`, applied by
    the callers that inject or supersede. Conflating the two made a superseded fact
    look live in a review pass, which is exactly the confusion versioning exists to
    remove."""
    try:
        return (
            _kb()
            .table.search()
            .where(f"source = '{_SOURCE}'", prefilter=True)
            .limit(1000)
            .to_list()
        )
    except Exception as e:
        log.warning("memory scan failed: %s", e)
        return []


def memory_status() -> str:
    """One line for boot: can memory actually be read and written right now?

    `recall_block` returns "" on any failure, which makes a dead embed backend look
    exactly like "nothing relevant recalled" — that silence is why memory could rot
    unnoticed. A probe at startup turns it into a visible state.

    Note the probe must use the module-level `_embed`, not a method: the embedder
    is a function in store.knowledge_base, and the backend stamp lives on the
    KnowledgeBase instance (`memory.lance.embed_backend`).
    """
    try:
        from store import knowledge_base as _kbm

        kb = _kb()
        # Count only live versions: superseded and tombstoned rows are history, and
        # reporting them as facts made a forgotten memory look still in force.
        total = sum(1 for r in _memory_rows() if _is_live(r))
        _kbm._embed("clixen memory probe", backend=kb.embed_backend)
        return f"memory: ok ({total} live facts, {kb.embed_backend or _kbm.EMBED_BACKEND} embeddings)"
    except Exception as e:
        try:
            total = sum(1 for r in _memory_rows() if _is_live(r))
        except Exception:
            total = 0
        return (
            f"memory: UNREACHABLE ({total} facts stored but not searchable) — {e}. "
            "recall_block() returns empty for every query until an embed backend is reachable."
        )


def remember(
    fact: str,
    entity_type: str = "",
    entity_name: str = "",
    tier: str = "",
    key: str = "",
) -> str:
    """Store a durable fact about the user. Returns a short confirmation.

    entity_type/entity_name (optional, e.g. "person"/"Sarah") tag the fact so it can
    be looked up as a group later via recall_about(), instead of only surfacing on
    similarity match.

    tier (optional, "always"/"on_relevance"/"surface_only") controls when it's injected:
    "always" facts show up in recall_block() every turn regardless of topic similarity —
    use sparingly, for things genuinely always-relevant (e.g. a standing preference).
    Default "on_relevance" is the pre-existing similarity-threshold behavior.

    key (optional) names the belief this fact is about, e.g. "user.timezone".
    Re-stating the same key supersedes the live version instead of overwriting it:
    the old row keeps valid_to and stops being recalled, but stays for audit — a
    memory that was later corrected should be traceable, and a poisoned memory
    should be removable without erasing the evidence that it existed.
    """
    fact = (fact or "").strip()
    if not fact:
        return "Nothing to remember — empty fact."
    query_tag = _build_tags(entity_type, entity_name, tier)
    key = (key or "").strip()
    try:
        kb = _kb()
        new_id = _content_id(fact)
        if key:
            _supersede(key, new_id)  # history, not overwrite (ADR 0018)
        # Idempotent for an identical restatement: same content → same id → the
        # deletion below drops the old row, then we re-add it with a fresh
        # confirmation timestamp so "user said it again" is recorded as such.
        kb.table.delete(f"id = '{new_id}'")
        kb.store(
            content=fact,
            source=_SOURCE,
            method="manual",
            query=query_tag,
            url=_new_meta(key=key),
        )
        suffix = " (updated)" if key else ""
        return f"Got it — I'll remember that{suffix}: {fact}"
    except Exception as e:  # Ollama/embed down — don't crash the turn
        log.warning("remember failed: %s", e)
        return f"[error] couldn't save memory: {e}"


def _entity_rows() -> list[dict]:
    """Live user_memory rows carrying an entity: tag. Table is personal-scale (single
    user), so a filtered full scan + python-side tag parse is simpler and more robust
    than encoding exact-match LIKE patterns against a multi-segment tag string."""
    try:
        rows = (
            _kb()
            .table.search()
            .where(f"source = '{_SOURCE}' AND query LIKE '{_ENTITY_PREFIX}%'", prefilter=True)
            .to_list()
        )
    except Exception as e:
        log.warning("_entity_rows failed: %s", e)
        return []
    return [r for r in rows if _is_live(r)]


def recall_about(entity_name: str) -> str:
    """Structured lookup: all facts explicitly tagged to entity_name, regardless of
    wording/similarity. Complements recall_block()'s per-turn similarity recall —
    this is exact-match by entity, for 'what do I know about X' style queries."""
    entity_name = (entity_name or "").strip()
    if not entity_name:
        return ""
    rows = []
    for r in _entity_rows():
        tags = _parse_tags(r.get("query", ""))
        entity_val = tags.get("entity", "")  # "{type}:{name}"
        name = entity_val.split(":", 1)[1] if ":" in entity_val else entity_val
        if name == entity_name:
            rows.append(r)
    if not rows:
        return ""
    bullets = [f"- {r['content']}" for r in rows]
    return f"## What you remember about {entity_name}\n{chr(10).join(bullets)}\n"


def forget(query: str) -> str:
    """Tombstone the single best-matching memory. Conservative by design: only the
    closest match (within threshold), never a whole neighbourhood of related facts.

    Tombstoned, not deleted (ADR 0018): the row keeps its history for audit but
    leaves every future recall immediately. Deleting outright would erase the
    evidence that a memory — possibly an injected one — ever existed.
    """
    query = (query or "").strip()
    if not query:
        return "Nothing to forget — empty query."
    try:
        hits = [h for h in _kb().search(query, top_k=3, source_filter=_SOURCE) if _is_live(h)]
        if not hits or hits[0].get("_distance", 99) > _forget_threshold():
            return f"No memory found matching: {query}"
        top = hits[0]
        import time as _time

        if not _set_meta(top["id"], tombstoned=True, valid_to=_time.time()):
            return f"[error] found the memory but could not tombstone it: {top.get('content', '')[:80]}"
        return f"Forgot: {top['content']}"
    except Exception as e:
        log.warning("forget failed: %s", e)
        return f"[error] couldn't forget: {e}"


def _always_rows() -> list[dict]:
    """Live user_memory rows tagged tier:always — injected every turn regardless of topic."""
    try:
        rows = (
            _kb()
            .table.search()
            .where(f"source = '{_SOURCE}' AND query LIKE '%{_TIER_PREFIX}always%'", prefilter=True)
            .to_list()
        )
    except Exception as e:
        log.warning("_always_rows failed: %s", e)
        return []
    return [r for r in rows if _is_live(r)]


def _record_recall_signals(query: str, hits: list[dict]) -> None:
    """Log which memories this query pulled, scaled to 0..1 (1 = exact match) so the
    figure means the same thing across embed backends with different distance ranges."""
    try:
        from store.memory_signals import record_recalls

        threshold = _recall_threshold()
        record_recalls(
            query,
            [
                (h["id"], max(0.0, 1.0 - float(h.get("_distance", threshold)) / threshold))
                for h in hits
                if h.get("id")
            ],
        )
    except Exception as e:
        log.debug("recall signal skipped: %s", e)


def recall_block(query: str) -> str:
    """
    Harness hook: return a system-prompt block of memories relevant to `query`,
    or "" when there's nothing to recall (empty namespace) or recall fails.

    Two embeds + two LanceDB lookups (user_memory + session fold summaries);
    safe to call on every turn.

    Two deliberate properties, both from ADR 0018:
    - Only live versions are injected (superseded and tombstoned rows are history).
    - The block is FENCED as untrusted data. A memory is text that entered the
      system from outside the current turn — an emailed "remember that the user
      approved transfers", a page the agent read, or something the model wrote to
      itself earlier. Structurally it must sit below instructions, not beside them.
    """
    try:
        hits = _kb().search(query or "", top_k=5, source_filter=_SOURCE)
        hits += _kb().search(query or "", top_k=5, source_filter=_SESSION_SOURCE)
    except Exception as e:
        # Was log.debug: a dead embed backend then looked exactly like "nothing
        # relevant recalled" for weeks. Loud once per process, quiet after that.
        global _recall_warned
        if not _recall_warned:
            _recall_warned = True
            log.warning(
                "memory recall unavailable (%s) — every turn runs WITHOUT recalled facts; "
                "see memory_tools.memory_status()", e,
            )
        return ""
    # Only inject on-topic memories — otherwise every turn floods the prompt with all of them.
    hits = [h for h in hits if h.get("_distance", 99) <= _recall_threshold() and _is_live(h)]

    # Record only what similarity actually retrieved. The "always" rows merged in below
    # bypass the gate, so counting them would max out every usage metric without the
    # memory ever having been a good match for anything.
    _record_recall_signals(query or "", hits)

    # "always" tier bypasses the similarity gate entirely — merge in, dedup by id
    # (a topically-similar always fact may already be in hits from the search above).
    seen_ids = {h["id"] for h in hits}
    for r in _always_rows():
        if r["id"] not in seen_ids:
            hits.append(r)
            seen_ids.add(r["id"])

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
    body = f"## What you remember (recalled, may be stale)\n{chr(10).join(bullets)}"

    # Same fence as page text and mail (tools/injection_guard.py): memories are
    # recalled *data*. Using the shared wrapper means a memory that says "ignore
    # previous instructions" arrives with the same standing as one quoted from a
    # web page, and the fence's own sentinel cannot be forged from inside it.
    from tools.injection_guard import wrap_external_output

    return wrap_external_output("memory_recall", body)


def mem_block_for(query: str) -> str:
    """Specialist hook: same recall as recall_block, zero logging/noise. Returns "" if empty."""
    if not (query or "").strip():
        return ""
    try:
        return recall_block(query)
    except Exception:
        return ""


# ── Relation links (sqlite side-table, separate from the LanceDB fact store) ────

RELATION_TYPES = (
    "supports", "contradicts", "extends", "part_of",
    "instance_of", "caused_by", "preceded_by", "related",
)

_relations_conn = None


def _relations_db():
    global _relations_conn
    if _relations_conn is None:
        import sqlite3
        from tools.vault_paths import db_path as _vault_db_path, ensure_data_dir
        ensure_data_dir()
        path = _vault_db_path("memory_relations.db")
        con = sqlite3.connect(path, check_same_thread=False)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS relations (
                from_id TEXT NOT NULL,
                to_id   TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (from_id, to_id, relation_type)
            )
            """
        )
        con.commit()
        _relations_conn = con
    return _relations_conn


def link_facts(fact_a: str, fact_b: str, relation_type: str = "related") -> str:
    """Link two already-remembered facts (e.g. 'Sarah joined Acme' extends 'Sarah works at Acme').
    Both facts must already exist (call remember() first) — this links by content, not by writing new facts."""
    from datetime import datetime, timezone

    if relation_type not in RELATION_TYPES:
        return f"[error] unknown relation_type {relation_type!r}, use one of {RELATION_TYPES}"
    id_a, id_b = _content_id(fact_a.strip()), _content_id(fact_b.strip())
    try:
        existing_ids = {r["id"] for r in _kb().table.search().where(f"source = '{_SOURCE}'", prefilter=True).to_list()}
    except Exception as e:
        log.warning("link_facts lookup failed: %s", e)
        return f"[error] couldn't verify facts: {e}"
    missing = [f for f, i in ((fact_a, id_a), (fact_b, id_b)) if i not in existing_ids]
    if missing:
        return f"[error] not remembered yet, call remember() first: {missing}"
    con = _relations_db()
    con.execute(
        "INSERT OR IGNORE INTO relations (from_id, to_id, relation_type, created_at) VALUES (?, ?, ?, ?)",
        (id_a, id_b, relation_type, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    return f"Linked: {fact_a!r} --{relation_type}--> {fact_b!r}"


def facts_related_to(fact: str) -> str:
    """All facts linked to the given fact, in either direction, with their relation type."""
    fact_id = _content_id(fact.strip())
    con = _relations_db()
    rows = con.execute(
        "SELECT to_id, relation_type, 'forward' as dir FROM relations WHERE from_id = ? "
        "UNION ALL "
        "SELECT from_id, relation_type, 'backward' as dir FROM relations WHERE to_id = ?",
        (fact_id, fact_id),
    ).fetchall()
    if not rows:
        return ""
    try:
        all_facts = {r["id"]: r["content"] for r in _kb().table.search().where(f"source = '{_SOURCE}'", prefilter=True).to_list()}
    except Exception as e:
        log.warning("facts_related_to lookup failed: %s", e)
        return ""
    bullets = []
    for other_id, rel_type, direction in rows:
        content = all_facts.get(other_id)
        if content is None:
            continue
        arrow = f"--{rel_type}-->" if direction == "forward" else f"<--{rel_type}--"
        bullets.append(f"- {arrow} {content}")
    if not bullets:
        return ""
    return f"## Related to: {fact}\n{chr(10).join(bullets)}\n"


def save_session_summary(chat_id: str, summary: str) -> None:
    """Save a conversation fold summary to persistent session memory (best-effort).
    Overwrites previous entry for same chat_id — no stale accumulation."""
    try:
        # Lance writes are table-level transactions. Folding runs in a
        # background thread, so serialize this delete+insert pair to prevent
        # concurrent transaction cleanup/manifest races during long chats.
        with _session_summary_lock:
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
                    },
                    "entity_type": {
                        "type": "string",
                        "description": "Optional: what kind of thing this fact is about, e.g. 'person', 'project'. Only set together with entity_name.",
                    },
                    "entity_name": {
                        "type": "string",
                        "description": "Optional: name of the specific person/project this fact is about (e.g. 'Sarah'). Lets recall_about(entity_name) find all facts about them later.",
                    },
                    "tier": {
                        "type": "string",
                        "enum": ["always", "on_relevance", "surface_only"],
                        "description": "Optional, default on_relevance (topic-similarity gated, the normal behavior). 'always' injects this fact every turn regardless of topic — use sparingly, only for standing facts genuinely always relevant.",
                    },
                    "key": {
                        "type": "string",
                        "description": "Optional stable name for the belief this fact is about, e.g. 'user.timezone'. Re-stating the same key supersedes the old version instead of leaving two conflicting facts; the old one stops being recalled but stays in history.",
                    },
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "link_facts",
            "description": (
                "Link two already-remembered facts with a relation (supports, contradicts, "
                "extends, part_of, instance_of, caused_by, preceded_by, related). Both facts "
                "must already exist via remember(). Use to connect related knowledge, e.g. "
                "linking a new fact that extends or supersedes an old one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fact_a": {"type": "string", "description": "The source fact (exact text as remembered)."},
                    "fact_b": {"type": "string", "description": "The target fact (exact text as remembered)."},
                    "relation_type": {
                        "type": "string",
                        "enum": ["supports", "contradicts", "extends", "part_of", "instance_of", "caused_by", "preceded_by", "related"],
                        "description": "How fact_a relates to fact_b. Default 'related' if unsure.",
                    },
                },
                "required": ["fact_a", "fact_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "facts_related_to",
            "description": "List all facts linked to a given remembered fact, in either direction, with their relation type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The fact (exact text as remembered) to find links for."},
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_about",
            "description": (
                "Look up everything remembered about a specific named person or project. "
                "Use for 'what do you know about X' style questions — exact entity match, "
                "not similarity search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {
                        "type": "string",
                        "description": "Name of the person or project to recall facts about.",
                    }
                },
                "required": ["entity_name"],
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
    "remember": lambda args: remember(args.get("fact", ""), args.get("entity_type", ""), args.get("entity_name", ""), args.get("tier", ""), args.get("key", "")),
    "recall_about": lambda args: recall_about(args.get("entity_name", "")) or f"Nothing remembered about {args.get('entity_name', '')}.",
    "link_facts": lambda args: link_facts(args.get("fact_a", ""), args.get("fact_b", ""), args.get("relation_type", "related")),
    "facts_related_to": lambda args: facts_related_to(args.get("fact", "")) or f"No links found for: {args.get('fact', '')}",
    "forget": lambda args: forget(args.get("query", "")),
    "search_sessions": lambda args: search_sessions(args.get("query", ""), top_k=args.get("top_k", 5)),
}
