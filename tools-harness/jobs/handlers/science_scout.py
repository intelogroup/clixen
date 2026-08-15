"""Handler: science_scout

Scans configured science niches (fixed keyword set, config-overridable —
no auto-discovery like reddit_intel, see store/science_scout_store.py's
module docstring for why) via SearXNG's science category, extracts full-text
claims from shortlisted papers with paper-qa, dedups/merges against the
persistent claim archive, classifies evidence strength on new claims, and
(weekly) emails a summary via notify_gate. Cloned from
jobs/handlers/reddit_intel.py — same dedup ladder and merge-decision shape,
different discovery source and no novelty scoring (see plan doc: LLM
self-rated novelty is an unreliable signal, dropped from scope).

instance["config"] keys:
  niche_queries  list[str] — defaults to DEFAULT_NICHE_QUERIES
  run_mode       "scan" (default) | "weekly_report"
"""
from __future__ import annotations

import asyncio
import json
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from store import science_scout_store as store

_log = logging.getLogger(__name__)


def _warn_failed(what: str, exc: BaseException) -> None:
    """One-line failure log — no traceback spam for expected/transient errors
    (unreachable URL, parse error, notify flake). Scan keeps going regardless."""
    _log.warning("[science_scout] %s: %s: %s", what, type(exc).__name__, exc)

DEFAULT_NICHE_QUERIES = [
    "microplastic-degrading enzyme discovery",
    "plastic-eating bacteria new species",
    "PETase enzyme engineering breakthrough",
]

# CREATE decisions at these levels ping the user immediately (via notify_gate),
# not just in the weekly digest — Predicted/Speculative wait for the digest.
STRONG_EVIDENCE_LEVELS = ("Observed", "Replicated")

# Cap the immediate (phone/agent-wake) notifications per niche per day. A niche
# query pulls a cluster of near-identical papers (same tissue-engineering story,
# different method) that each pass the semantic dedup — without a cap the scout
# rings the user every ~2 minutes with what reads as duplicates. Excess findings
# are still stored as claims and reach the weekly digest; only the interrupt is
# throttled.
NOTIFY_CAP_PER_NICHE_PER_DAY = 3

NEAR_DUP_MAX_DISTANCE = 0.65
MAX_RESULTS_PER_QUERY = 8
# Cap how many fresh candidates get the (slow, LLM-costly) paper-qa full-text
# pass per scan — the rest still get merge-decided off their search snippet.
# ponytail: most science URLs are news, not PDFs — paper-qa fails fast via
# Semantic Scholar, so keep extraction count low to avoid rate-limit waits.
MAX_PAPERQA_EXTRACTIONS_PER_SCAN = 2
PAPERQA_TIMEOUT_S = 20
QUERY_BATCH_SIZE = 3
QUERY_COOLDOWN_SCANS = 5
_QUERY_VARIANTS = (
    "mechanism",
    "replication evidence",
    "clinical translation",
    "real world application",
    "safety and failure modes",
    "recent study",
)

# ponytail: same model the rest of the harness uses for cloud calls — keeps
# paper-qa off its OpenAI default without adding a second provider to manage.
# NOTE: was "openrouter/deepseek/deepseek-v4-flash" — the openrouter/ prefix
# routes DeepSeek through OpenRouter, which 404s on the account's
# provider-restricted OpenRouter key (allowed: anthropic/cloudflare/
# google-ai-studio), so every paper-qa extraction silently degraded to
# snippet-only decisions. The harness default (deepseek/… without the
# openrouter/ prefix) hits DeepSeek's direct API instead.
from clients.cloud_client import DEFAULT_CLOUD_MODEL as _HARNESS_DEFAULT_MODEL
_PAPERQA_LLM = _HARNESS_DEFAULT_MODEL
_PAPERQA_EMBEDDING = "openai/text-embedding-3-small"


def _paperqa_llm() -> str:
    # 2026-08-01: paper-qa builds its own litellm Settings object, so it never
    # went through cloud_client's dead-provider/OpenAI-fallback machinery —
    # confirmed live, every extraction 402'd on the same exhausted OpenRouter
    # account (caught by extract_claim's best-effort try/except, so no crash,
    # just silently degraded to snippet-only merge decisions the whole time
    # OpenRouter stays dead). Route it through the same 24h dead-provider check
    # everything else uses.
    from clients.cloud_client import OPENAI_FALLBACK_MODEL, _is_dead
    return OPENAI_FALLBACK_MODEL if _is_dead(_PAPERQA_LLM) else _PAPERQA_LLM

MERGE_DECISION_SYSTEM_PROMPT = """You track findings in a narrow science niche: {niche}. \
Given ONE new paper (title, snippet, and full-text extract if available) and the closest \
existing claim (if any), decide exactly one of:
CREATE - no existing claim is a real match, this is a genuinely new finding
UPDATE - the paper is more evidence for the existing claim (same underlying finding)
IGNORE - not a real finding / too vague / off-topic / pure speculation with no evidence
CONTRADICT - the paper provides evidence AGAINST the existing claim's premise

Also classify evidence_level for the finding (CREATE/UPDATE only), one of exactly:
Observed | Replicated | Mechanistically supported | Predicted | Speculative | Impossible
(Observed = seen once in a real experiment. Replicated = reproduced by others. \
Mechanistically supported = a plausible mechanism is demonstrated, not just correlation. \
Predicted = model/simulation only, no wet-lab evidence. Speculative = hypothesis with no \
direct evidence. Impossible = contradicts established physics/chemistry/biology.)

Respond with ONLY a JSON object: {{"decision": "CREATE|UPDATE|IGNORE|CONTRADICT", \
"summary": "one-sentence finding description (only used for CREATE)", \
"call_detail": "4-6 sentences, only used for CREATE — the actual result a peer scientist \
would want: key numbers, methodology, sample size/scale, comparison to prior work, spoken \
aloud as a real briefing, not a headline", \
"evidence_level": "one of the six levels above"}}"""


_kb = None


def _get_knowledge_base():
    global _kb
    if _kb is None:
        from store.knowledge_base import KnowledgeBase
        from tools.vault_paths import db_path as _vault_db_path
        db_path = _vault_db_path("science_scout.lance")
        _kb = KnowledgeBase(db_path=db_path)
    return _kb


def collect_new_papers(niche_queries: list[str]) -> list[dict]:
    """Search each niche query via SearXNG's science category. Never raises —
    a search failure for one query just means fewer candidates this scan."""
    from tools.searxng_search import execute as searxng_execute

    papers: list[dict] = []
    for niche in niche_queries:
        try:
            result = searxng_execute(niche, categories="science", max_results=MAX_RESULTS_PER_QUERY)
        except Exception as e:
            _warn_failed(f"search failed for niche {niche!r}", e)
            continue
        if not result.ok:
            continue
        for item in result.items or []:
            if not item.url:
                continue
            papers.append({
                "id": item.url, "niche": niche, "title": item.title,
                "snippet": item.snippet, "url": item.url,
            })
    return papers


def _query_pool(queries: list[str]) -> list[str]:
    """Build a sufficiently large automatic rotation pool from seed queries."""
    pool = []
    seen = set()
    for query in queries:
        clean = " ".join(str(query or "").split())
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            pool.append(clean)
    if len(pool) >= QUERY_BATCH_SIZE * (QUERY_COOLDOWN_SCANS + 1):
        return pool
    for base in list(pool):
        for suffix in _QUERY_VARIANTS:
            candidate = f"{base} {suffix}"
            if candidate.casefold() not in seen:
                seen.add(candidate.casefold())
                pool.append(candidate)
            if len(pool) >= QUERY_BATCH_SIZE * (QUERY_COOLDOWN_SCANS + 1):
                return pool
    return pool


def _discover_query_candidates(seed_queries: list[str], scan_number: int) -> list[str]:
    """Ask the planner for fresh, cross-domain science topics every scan."""
    from clients.cloud_client import chat

    recent = store.recent_query_usage(limit=30)
    recent_text = ", ".join(row["query"] for row in recent) or "none"
    performance = store.query_performance(limit=30)
    performance_text = "; ".join(
        f"{row['query']} (scans={row['scans']}, hits={row['hits']}, rate={row['hit_rate']:.2f})"
        for row in performance
    ) or "none"
    claims = store.list_active_claims(limit=20)
    claim_text = "; ".join(c["summary"] for c in claims) or "none"
    prompt = (
        "You are the discovery planner for a science scout. Generate six fresh, "
        "high-signal search queries for the next scan. Maximize subject diversity: "
        "choose different areas such as medicine, biology, materials, space, geology, "
        "climate, energy, engineering, or algorithms. Queries must target recent "
        "discoveries or updates, be concrete enough for a science search engine, and "
        "not repeat or lightly reword recent queries. Return ONLY a JSON array of "
        "strings.\n\n"
        f"Scan number: {scan_number}\n"
        f"Recent queries to avoid: {recent_text}\n"
        f"Query performance: {performance_text}\n"
        f"Existing claims to extend or challenge: {claim_text}\n"
        f"Legacy seeds for context only: {', '.join(seed_queries[:12])}"
    )
    try:
        response = chat(user_message=prompt, reasoning_effort="low")
        text = str(response or "").strip()
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return []
        values = json.loads(text[start:end + 1])
        if not isinstance(values, list):
            return []
        out = []
        seen = set()
        for value in values:
            query = " ".join(str(value or "").split())
            key = query.casefold()
            if 8 <= len(query) <= 180 and key not in seen:
                seen.add(key)
                out.append(query)
        return out[:6]
    except Exception as exc:
        _log.warning("[science_scout] query discovery failed: %s", exc)
        return []


def _self_review_query_pool() -> None:
    """Periodically retire weak queries and replenish the long-lived pool."""
    from clients.cloud_client import chat

    performance = store.query_performance(limit=200)
    if not performance:
        return
    prompt = (
        "Review this science-search query performance. Retire queries that are "
        "repeatedly unproductive or too narrow, and propose replacements that "
        "increase subject diversity. Return ONLY JSON: "
        '{"retire": ["..."], "add": ["..."]}. Do not retire a query with a '
        "strong finding unless its future value is clearly exhausted.\n\n"
        + json.dumps(performance, default=str)
    )
    try:
        response = chat(user_message=prompt, reasoning_effort="low")
        text = str(response or "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return
        decision = json.loads(text[start:end + 1])
        retire = decision.get("retire", []) if isinstance(decision, dict) else []
        add = decision.get("add", []) if isinstance(decision, dict) else []
        if isinstance(retire, list):
            store.retire_queries(retire[:50])
        if isinstance(add, list):
            store.add_discovered_queries(add[:50])
        _log.info("[science_scout] query pool review: retired=%d added=%d",
                  len(retire) if isinstance(retire, list) else 0,
                  len(add) if isinstance(add, list) else 0)
    except Exception as exc:
        _log.warning("[science_scout] query pool review failed: %s", exc)


def exact_dedup(papers: list[dict]) -> list[dict]:
    fresh = []
    batch_ids = set()
    batch_hashes = set()
    for p in papers:
        pid = p.get("id", "")
        if not pid:
            continue
        chash = store.content_hash(p.get("title", ""), p.get("url", ""))
        if pid in batch_ids or chash in batch_hashes:
            continue
        if store.paper_exists(pid):
            store.touch_paper(pid)
            continue
        if store.find_by_content_hash(chash):
            continue
        batch_ids.add(pid)
        batch_hashes.add(chash)
        fresh.append(p)
    return fresh


async def _paperqa_extract_async(url: str, niche: str) -> str:
    from paperqa import Docs, Settings
    from paperqa.settings import AgentSettings

    llm = _paperqa_llm()
    settings = Settings(llm=llm, summary_llm=llm, embedding=_PAPERQA_EMBEDDING,
                        agent=AgentSettings())
    docs = Docs()
    docname = await docs.aadd_url(url, settings=settings)
    if not docname:
        return ""
    session = await docs.aquery(
        f"What specific new finding, in the context of {niche}, does this paper demonstrate? "
        "One or two sentences, cite only what's actually shown.",
        settings=settings,
    )
    return (session.answer or "").strip()


def extract_claim(url: str, niche: str) -> str:
    """Full-text extraction via paper-qa. Best-effort: any failure (unreachable
    PDF, parse error, timeout, missing dependency) falls back to '' — the
    caller then merge-decides off the search snippet instead, never blocking
    the scan on one bad paper."""
    try:
        return asyncio.run(asyncio.wait_for(_paperqa_extract_async(url, niche), timeout=PAPERQA_TIMEOUT_S))
    except Exception as e:
        _warn_failed(f"paper-qa extraction failed for {url}", e)
        return ""


def cluster_one(paper: dict, kb, near_dup_max_distance: float = NEAR_DUP_MAX_DISTANCE) -> dict:
    text = f"{paper.get('title', '')}\n{paper.get('extracted_claim') or paper.get('snippet', '')}"
    hits = kb.search(text, top_k=1, source_filter="science_claim")
    matched = None
    if hits and hits[0].get("_distance", 2.0) < near_dup_max_distance:
        matched = store.get_claim(hits[0]["query"])
    return {**paper, "matched_claim": matched}


def llm_merge_decision(cluster: dict) -> dict:
    from clients.cloud_client import chat
    from clients.cost_guard import BudgetExceededError

    matched = cluster.get("matched_claim")
    body = cluster.get("extracted_claim") or cluster.get("snippet", "")
    prompt = (
        f"New paper: {cluster.get('title')}\n{body[:1500]}\n\n"
        + (f"Closest existing claim: {matched['summary']}" if matched else "No close existing claim found.")
    )
    system_prompt = store.get_prompt("merge_decision", MERGE_DECISION_SYSTEM_PROMPT).format(
        niche=cluster.get("niche", "this niche")
    )
    try:
        resp = chat(user_message=prompt, system_prompt=system_prompt, reasoning_effort="low")
    except BudgetExceededError:
        return {"decision": "IGNORE"}
    try:
        start, end = resp.find("{"), resp.rfind("}")
        return json.loads(resp[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return {"decision": "IGNORE"}


def apply_decision(paper: dict, decision: dict, kb) -> str:
    matched = paper.get("matched_claim")
    verdict = decision.get("decision", "IGNORE")

    embedding_id = ""
    text = f"{paper.get('title', '')}\n{paper.get('extracted_claim') or paper.get('snippet', '')}"
    try:
        ids = kb.store(text, source="science_paper", query=paper.get("id", ""))
        embedding_id = ids[0] if ids else ""
    except Exception as e:
        _warn_failed("embedding store failed, continuing without it", e)

    store.insert_paper(
        paper_id=paper.get("id", ""), niche=paper.get("niche", ""),
        title=paper.get("title", ""), snippet=paper.get("snippet", ""),
        url=paper.get("url", ""), extracted_claim=paper.get("extracted_claim", ""),
        embedding_id=embedding_id,
    )

    if verdict == "CREATE":
        summary = decision.get("summary", paper.get("title", ""))
        evidence_level = decision.get("evidence_level", "Speculative")
        claim_id = store.create_claim(
            summary=summary, evidence_level=evidence_level, niches=[paper.get("niche", "")],
        )
        try:
            kb.store(decision.get("summary", ""), source="science_claim", query=claim_id)
        except Exception as e:
            _warn_failed("claim embedding store failed", e)
        store.link(paper.get("id", ""), claim_id, "CREATE")
        if evidence_level in STRONG_EVIDENCE_LEVELS:
            try:
                from jobs.notify_gate import decide_and_notify
                from store import world_monitor_store as wm_store
                call_detail = decision.get("call_detail", "") or summary
                finding_text = f"New {evidence_level.lower()} finding in {paper.get('niche', 'science scout')}: {call_detail}"
                # Shared cross-source dedup ledger (world_monitor_store) on top of this
                # handler's own paper/embedding novelty gate — catches the case where
                # a semantically "new" claim still restates something already surfaced
                # (by science_scout or any other source) recently.
                # Dedup key is call_detail, NOT finding_text: finding_text's stable
                # "New {level} finding in {niche}:" prefix always contains the first
                # ":" — dedup_key() cuts there, collapsing every distinct paper in
                # the same niche onto one key (verified live 2026-08-04, 270/270
                # findings that day suppressed as false dupes across ~30 niches).
                if not wm_store.claim_notified(call_detail, "", "science_scout"):
                    store.log_suppressed_alert("science_scout", finding_text, "already surfaced (cross-source dedup)")
                else:
                    niche = paper.get("niche", "science scout")
                    if store.niche_notify_count(niche) >= NOTIFY_CAP_PER_NICHE_PER_DAY:
                        store.log_suppressed_alert(
                            "science_scout", finding_text,
                            f"rate-limited: {NOTIFY_CAP_PER_NICHE_PER_DAY} immediate notifications/day for niche '{niche}'",
                        )
                    else:
                        store.record_niche_notify(niche)
                        decide_and_notify(
                            finding=finding_text,
                            source="science_scout", fallback_alert=True, wake_agent=True, call_phone=True, bypass_gate=True,
                            on_suppress=lambda finding, reason: store.log_suppressed_alert("science_scout", finding, reason),
                        )
            except Exception as e:
                _warn_failed("immediate notify failed", e)
    elif verdict == "UPDATE" and matched:
        store.bump_claim(matched["id"], paper.get("niche", ""), decision.get("evidence_level"))
        store.link(paper.get("id", ""), matched["id"], "UPDATE")
    elif verdict == "CONTRADICT" and matched:
        store.link(paper.get("id", ""), matched["id"], "CONTRADICT")
    return verdict


def _evolve_niches(current_queries: list[str]) -> None:
    """Drop worst-performing niche, ask LLM to generate a new one from top performers."""
    from clients.cloud_client import chat
    from clients.cost_guard import BudgetExceededError

    top = store.top_niches(limit=5)
    bottom = store.bottom_niches(limit=1, min_scans=3)

    _log.info("[science_scout] niche evolution: %d top, %d bottom candidates", len(top), len(bottom))

    if not bottom:
        return  # not enough data yet

    worst = bottom[0]
    _log.info("[science_scout] dropping underperforming niche: %s (hit_rate=%.2f)", worst["niche"], worst.get("hit_rate", 0))

    top_list = ", ".join(f"{n['niche']}({n['hits']}h/{n['scans']}scans)" for n in top)
    prompt = (
        f"Current top science niche queries (by hit rate):\n{top_list}\n\n"
        f"The query '{worst['niche']}' underperformed (hit_rate={worst.get('hit_rate', 0):.2f}) and will be replaced. "
        "Generate ONE new niche query (a targeted science research search term) that complements the top performers. "
        f"The user is a scientist, engineer, and doctor. Return ONLY a JSON string, no explanation."
    )
    try:
        resp = chat(user_message=prompt, reasoning_effort="low")
    except (BudgetExceededError, Exception) as e:
        _log.warning("[science_scout] niche evolution LLM call failed: %s", e)
        return

    try:
        cleaned = _extract_query_string(resp)
        if not cleaned or cleaned.lower() == "null":
            return
        new_queries = [q.strip() for q in current_queries if q != worst["niche"]]
        new_queries.append(cleaned)
        store.set_config("niche_queries", new_queries)
        _log.info("[science_scout] replaced niche '%s' with '%s'", worst["niche"], cleaned)
    except Exception as e:
        _log.warning("[science_scout] niche evolution parse failed: %s", e)


def _extract_query_string(resp: str) -> str:
    """LLM asked for 'ONLY a JSON string' but may return a bare string, a
    quoted/bracketed string, or a markdown-fenced JSON object/array — normalize
    all of them to the plain query text. A raw fenced blob slipping through
    silently becomes a permanent, garbage search query (only caught in prod
    logs, not by any test), so this must not fail open to the raw text."""
    text = resp.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return text.strip("[]\"' \n")
    if isinstance(parsed, str):
        return parsed.strip()
    if isinstance(parsed, list) and parsed:
        return str(parsed[0]).strip()
    if isinstance(parsed, dict):
        for key in ("query", "new_query"):
            if isinstance(parsed.get(key), str):
                return parsed[key].strip()
        for value in parsed.values():
            if isinstance(value, str):
                return value.strip()
    return ""


def regenerate_exec_summary() -> str:
    from clients.cloud_client import chat
    claims = store.list_active_claims(limit=30)
    if not claims:
        return "No active claims yet."
    lines = [f"- {c['summary']} (evidence={c['evidence_level']}, corroborations={c['evidence_count']})" for c in claims]
    prompt = "Summarize these science-niche findings into a short briefing (under 300 words):\n" + "\n".join(lines)
    return chat(user_message=prompt, reasoning_effort="low")


def handle(instance: dict) -> dict:
    """Never raise: return success:False so the worker's failure-notify path
    fires and a partial scan stays logged for inspection."""
    try:
        return _run_scan(instance)
    except Exception as exc:
        _log.error("[science_scout] scan failed: %s: %s", type(exc).__name__, exc)
        return {"success": False, "error": str(exc)}


def _run_scan(instance: dict) -> dict:
    cfg = instance.get("config", {})
    run_mode = cfg.get("run_mode", "scan")

    near_dup_max_distance = store.get_config("near_dup_max_distance", NEAR_DUP_MAX_DISTANCE)
    configured_queries = cfg.get("niche_queries")
    seed_queries = configured_queries or store.get_config("niche_queries", DEFAULT_NICHE_QUERIES)
    scan_number = (store.get_config("scan_count", 0) or 0) + 1
    if configured_queries:
        niche_queries = seed_queries
    else:
        discovered = _discover_query_candidates(seed_queries, scan_number)
        if discovered:
            store.add_discovered_queries(discovered)
        candidate_pool = discovered + store.discovered_queries() + seed_queries
        niche_queries = store.select_query_batch(
            _query_pool(candidate_pool), scan_number,
            count=QUERY_BATCH_SIZE, cooldown_scans=QUERY_COOLDOWN_SCANS,
        )

    kb = _get_knowledge_base()
    raw_papers = collect_new_papers(niche_queries)
    fresh = exact_dedup(raw_papers)

    # Parallel paper-qa extractions (slowest part)
    extract_targets = fresh[:MAX_PAPERQA_EXTRACTIONS_PER_SCAN]
    if extract_targets:
        with ThreadPoolExecutor(max_workers=min(8, len(extract_targets))) as ex:
            fut_map = {ex.submit(extract_claim, p["url"], p["niche"]): i for i, p in enumerate(extract_targets)}
            for f in as_completed(fut_map):
                idx = fut_map[f]
                try:
                    extract_targets[idx]["extracted_claim"] = f.result()
                except Exception:
                    extract_targets[idx]["extracted_claim"] = ""

    # Read-only embedding/vector searches can run in parallel. Keep merge
    # decisions and KB writes sequential because the LanceDB writer is not
    # thread-safe. This removes one embedding/search round-trip per paper from
    # the critical path without changing claim ordering.
    if fresh:
        with ThreadPoolExecutor(max_workers=min(4, len(fresh))) as ex:
            clustered_papers = list(
                ex.map(lambda paper: cluster_one(paper, kb, near_dup_max_distance), fresh)
            )
    else:
        clustered_papers = []

    # Sequential: merge decision + apply (KB writes are not thread-safe)
    processed = 0
    for clustered in clustered_papers:
        decision = llm_merge_decision(clustered)
        apply_decision(clustered, decision, kb)
        processed += 1

    # Track niche performance per decision
    _niche_hits = {}
    _niche_misses = {}
    for paper in fresh:
        n = paper.get("niche", "unknown")
        # Re-derive decision from store state: paper exists + linked claim = hit
        if store.paper_exists(paper.get("id", "")):
            _niche_hits[n] = _niche_hits.get(n, 0) + 1
        else:
            _niche_misses[n] = _niche_misses.get(n, 0) + 1
    for n in niche_queries:
        hits = _niche_hits.get(n, 0)
        misses = _niche_misses.get(n, 0)
        if hits > 0 or misses > 0:
            has_hit = hits > misses
            store.record_niche_perf(n, has_hit)

    # Every 10 runs, evolve niches: drop worst performer, generate replacement
    store.set_config("scan_count", scan_number)
    if scan_number % 10 == 0:
        _self_review_query_pool()
        _evolve_niches(seed_queries)

    result = {
        "success": True,
        "papers_found": len(raw_papers),
        "new_papers": len(fresh),
        "papers_processed": processed,
        "niches_scanned": niche_queries,
    }

    if run_mode == "weekly_report":
        report_fingerprint = store.active_claims_fingerprint()
        if store.get_meta("last_weekly_report_fingerprint") == report_fingerprint:
            result["weekly_report_skipped"] = True
            return result
        summary = regenerate_exec_summary()
        try:
            from jobs.notify_gate import decide_and_notify
            decide_and_notify(
                finding=summary, source="science_scout", fallback_alert=True,
                on_suppress=lambda finding, reason: store.log_suppressed_alert("science_scout", finding, reason),
            )
            store.set_meta("last_weekly_report_fingerprint", report_fingerprint)
        except Exception as e:
            _warn_failed("weekly notification failed", e)
        result["summary"] = summary

    return result
