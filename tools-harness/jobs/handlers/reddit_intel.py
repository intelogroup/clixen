"""Handler: reddit_intel

Scans configured subreddits for pain points / market opportunities, dedups
against the persistent archive (store/reddit_intel_store.py), clusters new
posts into insights via one LLM merge-decision call per candidate cluster,
and (weekly) emails a summary. Reports to the main orchestrator via the
ask_reddit_agent tool (tools/orchestrator_tools.py), which queries the
insight DB directly — this handler never answers chat queries itself.

Dedup ladder (cheapest first): exact id -> exact content hash -> semantic
near-dup (embedding cosine vs raw_items) -> semantic cluster vs existing
insights -> LLM CREATE/UPDATE/IGNORE/CONTRADICT decision. A post that matches
an existing insight never creates a new one; it only bumps evidence_count.

instance["config"] keys:
  subreddits   list[str] — defaults to a starter set of business/SaaS subs
  run_mode     "scan" (default) | "weekly_report"
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from store import reddit_intel_store as store

_log = logging.getLogger(__name__)

DEFAULT_SUBREDDITS = [
    "Entrepreneur", "SaaS", "SideProject", "StartupIdeas", "microsaas",
]

# ponytail: near-dup threshold tuned to KnowledgeBase's own is_cached() default
# (0.65 = same topic, different wording). Revisit if false-positive merges show up.
NEAR_DUP_MAX_DISTANCE = 0.65

# Below this many active subreddits, ask the LLM to discover replacements.
MIN_ACTIVE_SUBREDDITS = 4
# Don't re-fire discover_subreddits() more than once per this many hours —
# a bad discovery batch that keeps getting dropped would otherwise re-trigger
# a cloud LLM call every single scan cycle.
DISCOVERY_COOLDOWN_HOURS = 1
_DISCOVERY_META_KEY = "last_discovery_attempt_at"
# A subreddit that's produced neither a CREATE nor an UPDATE in this many
# consecutive scans is treated as dead and dropped.
DEAD_SCAN_THRESHOLD = 5

DISCOVERY_NICHE = "SaaS/startup pain points, unmet market needs, willingness to pay for a fix"

DISCOVERY_SYSTEM_PROMPT = """You suggest Reddit subreddits worth scanning for market \
intelligence: real people describing pain points, workarounds, or "I wish there was a \
tool for X" complaints related to: {niche}

Respond with ONLY a JSON array of subreddit names (no r/ prefix, no prose), e.g. \
["Entrepreneur", "smallbusiness"]. Do not repeat any of these already-watched subreddits: \
{existing}"""

MERGE_DECISION_SYSTEM_PROMPT = """You cluster Reddit posts about market pain points \
into a persistent insight database. Given ONE new post and the closest existing \
insight (if any), decide exactly one of:
CREATE - no existing insight is a real match, this is a genuinely new pain point
UPDATE - the post is more evidence for the existing insight (same underlying problem)
IGNORE - not a real pain point / too vague / off-topic
CONTRADICT - the post provides evidence AGAINST the existing insight's premise

Respond with ONLY a JSON object: {"decision": "CREATE|UPDATE|IGNORE|CONTRADICT", \
"summary": "one-sentence pain point description (only used for CREATE)", \
"severity": 0.0-1.0, "willingness_to_pay": 0.0-1.0}"""


_kb = None


def _get_knowledge_base():
    """Lazy singleton — LanceDB table-open cost paid once per process, not
    once per handle() call (this ran on every scheduled scan before)."""
    global _kb
    if _kb is None:
        from store.knowledge_base import KnowledgeBase
        from pathlib import Path
        db_path = str(Path(__file__).parent.parent.parent / "data" / "reddit_intel.lance")
        _kb = KnowledgeBase(db_path=db_path)
    return _kb


def discover_subreddits(existing: list[str], n: int = 3) -> list[tuple[str, str]]:
    """Ask the LLM for `n` new subreddits to watch, given what's already active.
    Returns [(name, reason)] — reason is just the niche string, kept for the
    discovered_reason column's audit trail. Malformed/empty response -> [].
    """
    from clients.cloud_client import chat
    from clients.cost_guard import BudgetExceededError

    system_prompt = store.get_prompt("discovery", DISCOVERY_SYSTEM_PROMPT)
    prompt = system_prompt.format(niche=DISCOVERY_NICHE, existing=", ".join(existing) or "none")
    try:
        resp = chat(user_message=f"Suggest {n} subreddits.", system_prompt=prompt, reasoning_effort="low")
    except BudgetExceededError:
        return []
    names = _extract_json_array(resp)
    return [(name.strip(), DISCOVERY_NICHE) for name in names if isinstance(name, str) and name.strip()]


_POST_ID_RE = re.compile(r"/comments/([a-z0-9]+)/")


def collect_new_posts(subreddits: list[str], run_id: str | None = None) -> list[dict]:
    """Fetch recent posts via SearXNG (DDG fallback) — reddit's own endpoints
    (anonymous .json, browser-agent scrape) are both blocked (403/login wall,
    confirmed live 2026-07-28). Search-engine indexed snippets only: no score/
    num_comments (search doesn't carry them), id parsed from the reddit URL.
    Never raises — a fetch failure just means an empty scan, not a crashed job.
    """
    from tools.searxng_search import execute as searxng_execute

    posts: list[dict] = []
    for sub in subreddits:
        try:
            result = searxng_execute(f"site:reddit.com/r/{sub}", max_results=15)
            items = result.items or [] if result and result.ok else []
            if not items:
                _log.warning("[reddit_intel] empty search result for r/%s", sub)
            for item in items:
                url = item.url
                m = _POST_ID_RE.search(url)
                if not m or f"/r/{sub.lower()}/" not in url.lower():
                    continue
                posts.append({
                    "id": m.group(1),
                    "title": item.title,
                    "body": item.snippet,
                    "url": url,
                    "score": 0,
                    "num_comments": 0,
                    "subreddit": sub,
                })
        except Exception:
            _log.warning("[reddit_intel] collect failed for r/%s", sub, exc_info=True)
    return posts


def _extract_json_array(text: str) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def exact_dedup(posts: list[dict]) -> list[dict]:
    """Drop posts already seen by id or by normalized content hash."""
    fresh = []
    for p in posts:
        pid = p.get("id", "")
        if not pid:
            continue
        if store.item_exists(pid):
            store.touch_item(pid)
            continue
        chash = store.content_hash(p.get("title", ""), p.get("body", ""))
        if store.find_by_content_hash(chash):
            continue
        fresh.append(p)
    return fresh


def cluster_one(post: dict, kb, near_dup_max_distance: float = NEAR_DUP_MAX_DISTANCE) -> dict:
    """Attach the closest existing insight (if any, within NEAR_DUP_MAX_DISTANCE)
    so the LLM merge call has something concrete to compare against.
    `matched_insight` is None for genuinely new territory.

    Must run per-post right before that post's decision, not batched upfront —
    otherwise two near-duplicate posts in the same scan both get CREATE, since
    the first post's insight doesn't exist yet when the batch is clustered
    (found via dry run 2026-07-27).
    """
    text = f"{post.get('title', '')}\n{post.get('body', '')}"
    hits = kb.search(text, top_k=1, source_filter="reddit_insight")
    matched = None
    if hits and hits[0].get("_distance", 2.0) < near_dup_max_distance:
        matched = store.get_insight(hits[0]["query"])  # query field holds insight_id
    return {**post, "matched_insight": matched}


def llm_merge_decision(cluster: dict) -> dict:
    from clients.cloud_client import chat
    from clients.cost_guard import BudgetExceededError

    matched = cluster.get("matched_insight")
    prompt = (
        f"New post (r/{cluster.get('subreddit')}): {cluster.get('title')}\n{cluster.get('body', '')[:1000]}\n\n"
        + (f"Closest existing insight: {matched['summary']}" if matched else "No close existing insight found.")
    )
    system_prompt = store.get_prompt("merge_decision", MERGE_DECISION_SYSTEM_PROMPT)
    try:
        resp = chat(user_message=prompt, system_prompt=system_prompt, reasoning_effort="low")
    except BudgetExceededError:
        return {"decision": "IGNORE"}
    try:
        start, end = resp.find("{"), resp.rfind("}")
        return json.loads(resp[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return {"decision": "IGNORE"}


def apply_decision(post: dict, decision: dict, kb) -> str:
    matched = post.get("matched_insight")
    verdict = decision.get("decision", "IGNORE")

    embedding_id = ""
    text = f"{post.get('title', '')}\n{post.get('body', '')}"
    try:
        ids = kb.store(text, source="reddit_item", query=post.get("id", ""))
        embedding_id = ids[0] if ids else ""
    except Exception:
        _log.warning("[reddit_intel] embedding store failed, continuing without it", exc_info=True)

    store.insert_item(
        item_id=post.get("id", ""), subreddit=post.get("subreddit", ""),
        title=post.get("title", ""), body=post.get("body", ""), url=post.get("url", ""),
        score=int(post.get("score", 0) or 0), num_comments=int(post.get("num_comments", 0) or 0),
        embedding_id=embedding_id,
    )

    if verdict == "CREATE":
        insight_id = store.create_insight(
            summary=decision.get("summary", post.get("title", "")),
            subreddits=[post.get("subreddit", "")],
            severity=float(decision.get("severity", 0) or 0),
            willingness_to_pay=float(decision.get("willingness_to_pay", 0) or 0),
        )
        try:
            kb.store(decision.get("summary", ""), source="reddit_insight", query=insight_id)
        except Exception:
            _log.warning("[reddit_intel] insight embedding store failed", exc_info=True)
        store.link(post.get("id", ""), insight_id, "CREATE")
    elif verdict == "UPDATE" and matched:
        store.bump_insight(matched["id"], post.get("subreddit", ""))
        store.link(post.get("id", ""), matched["id"], "UPDATE")
    elif verdict == "CONTRADICT" and matched:
        store.link(post.get("id", ""), matched["id"], "CONTRADICT")
    # IGNORE: item still recorded above (for future exact-dedup), no insight link
    return verdict


def regenerate_exec_summary() -> str:
    from clients.cloud_client import chat
    insights = store.list_active_insights(limit=30)
    if not insights:
        return "No active insights yet."
    lines = [f"- {i['summary']} (evidence={i['evidence_count']}, wtp={i['willingness_to_pay']:.2f})" for i in insights]
    prompt = "Summarize these Reddit market-intelligence insights into a short exec briefing (under 300 words):\n" + "\n".join(lines)
    return chat(user_message=prompt, reasoning_effort="low")


# Adaptive cadence: signal_ratio = fraction of scanned subreddits that produced
# a CREATE/UPDATE this run. Hot -> check back soon, quiet -> back off. The base
# schedule's interval_seconds is just a fallback if this override never fires.
_CADENCE_HOURS = [(0.3, 3), (0.0, 6)]  # (min_signal_ratio, delay_hours), checked in order
_CADENCE_QUIET_HOURS = 24


def _next_scan_delay_hours(signal_ratio: float) -> int:
    for min_ratio, hours in _CADENCE_HOURS:
        if signal_ratio > min_ratio:
            return hours
    return _CADENCE_QUIET_HOURS


def _discovery_cooldown_elapsed() -> bool:
    last = store.get_meta(_DISCOVERY_META_KEY)
    if not last:
        return True
    cooldown_hours = store.get_config("discovery_cooldown_hours", DISCOVERY_COOLDOWN_HOURS)
    elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last)
    return elapsed >= timedelta(hours=cooldown_hours)


def handle(instance: dict) -> dict:
    cfg = instance.get("config", {})
    run_mode = cfg.get("run_mode", "scan")
    run_id = instance.get("id")

    # DB-backed overrides (auto-tunable by subagent_audit) fall back to the
    # hardcoded module constants when no override has ever been written.
    near_dup_max_distance = store.get_config("near_dup_max_distance", NEAR_DUP_MAX_DISTANCE)
    min_active_subreddits = store.get_config("min_active_subreddits", MIN_ACTIVE_SUBREDDITS)
    dead_scan_threshold = store.get_config("dead_scan_threshold", DEAD_SCAN_THRESHOLD)

    # ── which subreddits: config override > watched-list (self-maintained) > seed
    if cfg.get("subreddits"):
        subreddits = cfg["subreddits"]
    else:
        if not store.list_active_subreddits():
            store.seed_watched_subreddits(DEFAULT_SUBREDDITS)
        active = store.list_active_subreddits()
        discovered = []
        if len(active) < min_active_subreddits and _discovery_cooldown_elapsed():
            for name, reason in discover_subreddits(active, n=min_active_subreddits - len(active)):
                store.add_subreddit(name, reason)
                discovered.append(name)
            store.set_meta(_DISCOVERY_META_KEY, datetime.now(timezone.utc).isoformat())
            active = store.list_active_subreddits()
        subreddits = active

    kb = _get_knowledge_base()
    raw_posts = collect_new_posts(subreddits, run_id=run_id)
    fresh = exact_dedup(raw_posts)

    processed = 0
    signal_by_subreddit: dict[str, bool] = {sub: False for sub in subreddits}
    for post in fresh:
        clustered_post = cluster_one(post, kb, near_dup_max_distance)
        decision = llm_merge_decision(clustered_post)
        verdict = apply_decision(clustered_post, decision, kb)
        if verdict in ("CREATE", "UPDATE"):
            signal_by_subreddit[post.get("subreddit", "")] = True
        processed += 1

    # Config-supplied subreddit lists aren't tracked in watched_subreddits
    # (they bypass discovery entirely) — only self-managed lists get drop/cadence.
    dropped = []
    if not cfg.get("subreddits"):
        for sub in subreddits:
            store.record_scan_outcome(sub, signal_by_subreddit.get(sub, False))
            if store.get_scans_since_new(sub) >= dead_scan_threshold:
                store.drop_subreddit(sub)
                dropped.append(sub)

    result = {
        "success": True,
        "posts_fetched": len(raw_posts),
        "new_posts": len(fresh),
        "items_processed": processed,
        "subreddits_scanned": subreddits,
        "dropped_subreddits": dropped,
    }

    if not cfg.get("subreddits") and subreddits:
        signal_ratio = sum(signal_by_subreddit.values()) / len(subreddits)
        delay_hours = _next_scan_delay_hours(signal_ratio)
        result["next_scan_delay_hours"] = delay_hours
        workflow_id = instance.get("id")
        if workflow_id:
            try:
                from store.workflow_store import update_workflow_instance
                next_run_at = (datetime.now(timezone.utc) + timedelta(hours=delay_hours)).isoformat()
                update_workflow_instance(workflow_id, next_run_at=next_run_at)
            except Exception:
                _log.warning("[reddit_intel] adaptive cadence update failed", exc_info=True)

    if run_mode == "weekly_report":
        summary = regenerate_exec_summary()
        try:
            from jobs.notify_gate import decide_and_notify
            # fallback_alert=True preserves the old always-notify behavior if
            # the decision call itself fails (budget/parse error) — this only
            # adds judgment on top, never makes weekly reports less reliable.
            decide_and_notify(
                finding=summary, source="reddit_intel", fallback_alert=True,
                on_suppress=lambda finding, reason: store.log_suppressed_alert("reddit_intel", finding, reason),
            )
        except Exception:
            _log.warning("[reddit_intel] weekly notification failed", exc_info=True)
        result["summary"] = summary

    return result
