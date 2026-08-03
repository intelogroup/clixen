"""Handler: world_monitor

Periodically queries free public global-intelligence sources (no API key needed)
for interesting events across science, health, conflict, climate, energy, and
tech. Uses LLM to judge what's worth surfacing given the user's profile as a
scientist, engineer, and doctor. Interesting findings trigger notify_gate with
call_phone=True — so the main agent can call the user about them.

instance["config"] keys:
  poll_hours  int — hours between scans (default 4)
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone

from jobs.notify_gate import decide_and_notify
from store import world_monitor_store as store

_log = logging.getLogger(__name__)

WORLD_MONITOR_SYSTEM_PROMPT = """You are a research director monitoring global intelligence for \
a scientist, engineer, and doctor. Given the raw world data below, identify findings that are \
genuinely interesting — novel research, engineering breakthroughs, emerging health threats, \
energy/climate developments with real-world impact, unusual geopolitical signals, or anything \
you'd excitedly tell a curious colleague about. Skip routine/minor events.

For each finding, write a "reason" a peer scientist would find substantive, not just a hook — \
include the actual result (key numbers, methodology, sample size/scale, comparison to prior \
work) in 4-6 sentences, enough to be read aloud as a real spoken briefing, not a headline. \
Return ONLY a JSON list: [{"finding": "...", "reason": "..."}] or [] if nothing worth surfacing."""


_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def _fetch(url: str, timeout: int = 15) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode(errors="replace")
    except Exception as e:
        _log.debug("[world_monitor] fetch failed %s: %s", url[:60], e)
        return None


def _query_sources() -> dict[str, str]:
    results = {}

    # arXiv new papers (latest, cross-listed in multiple categories = broad interest)
    body = _fetch("http://export.arxiv.org/api/query?search_query=all:new&sortBy=submittedDate&sortOrder=descending&max_results=10")
    if body:
        titles = re.findall(r"<title>(.*?)</title>", body)
        summaries = re.findall(r"<summary>(.*?)</summary>", body)
        lines = []
        for i, t in enumerate(titles[1:8]):  # skip feed-level title
            s = re.sub(r"\s+", " ", summaries[i][:200]) if i < len(summaries) else ""
            lines.append(f"{t.strip()} — {s}")
        if lines:
            results["new_research_papers"] = "\n".join(lines)

    # Evolved arXiv queries (top keyword-driven)
    for q in store.get_evolved_queries():
        label = f"arxiv_evolved_{q[:30]}"
        lines = _fetch_arxiv(q, label, max_results=4)
        if lines:
            results[label] = "\n".join(lines)

    # WHO disease outbreak news
    body = _fetch("https://www.who.int/rss-feeds/news-english.xml")
    if body:
        titles = re.findall(r"<title>(.*?)</title>", body)
        descs = re.findall(r"<description>(.*?)</description>", body)
        lines = []
        for i, t in enumerate(titles[1:8]):
            d = re.sub(r"\s+", " ", descs[i + 1][:150]) if i + 1 < len(descs) else ""
            lines.append(f"{t} — {d}")
        if lines:
            results["health_news"] = "\n".join(lines)

    # Hacker News top stories
    body = _fetch("https://hacker-news.firebaseio.com/v0/topstories.json")
    if body:
        try:
            ids = json.loads(body)[:10]
            items = []
            for sid in ids:
                item = _fetch(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json")
                if item:
                    try:
                        d = json.loads(item)
                        items.append(f"{d.get('title','?')} ({d.get('score',0)} pts)")
                    except json.JSONDecodeError:
                        pass
            if items:
                results["hacker_news"] = "\n".join(items)
        except json.JSONDecodeError:
            pass

    # Reddit r/science hot
    body = _fetch("https://www.reddit.com/r/science/hot.json?limit=10", timeout=10)
    if body:
        try:
            data = json.loads(body)
            kids = data.get("data", {}).get("children", [])
            if kids:
                lines = []
                for k in kids:
                    d = k.get("data", {})
                    lines.append(f"{d.get('title','?')} (↑{d.get('ups',0)})")
                results["reddit_science"] = "\n".join(lines)
        except json.JSONDecodeError:
            pass

    # NOAA space weather (solar flares, geomagnetic storms)
    body = _fetch("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    if body:
        try:
            data = json.loads(body)
            if isinstance(data, list) and len(data) > 1 and len(data[1]) > 1:
                results["solar_activity"] = f"K-index: {data[1][1]} at {data[1][0]} UTC"
        except (json.JSONDecodeError, IndexError, KeyError):
            pass

    # CDC public health news
    body = _fetch("https://tools.cdc.gov/api/v2/resources/media/404501.rss")
    if body:
        titles = re.findall(r"<title>(.*?)</title>", body)
        descs = re.findall(r"<description>(.*?)</description>", body)
        lines = []
        for i, t in enumerate(titles[1:6]):
            d = re.sub(r"\s+", " ", descs[i + 1][:150]) if i + 1 < len(descs) else ""
            lines.append(f"{t} — {d}")
        if lines:
            results["cdc_health"] = "\n".join(lines)

    return results


def _fetch_arxiv(query: str, label: str, max_results: int = 5) -> list[str]:
    url = f"http://export.arxiv.org/api/query?search_query={query}&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
    body = _fetch(url)
    if not body:
        return []
    titles = re.findall(r"<title>(.*?)</title>", body)
    summaries = re.findall(r"<summary>(.*?)</summary>", body)
    lines = []
    for i, t in enumerate(titles[1:max_results + 1]):
        s = re.sub(r"\s+", " ", summaries[i][:200]) if i < len(summaries) else ""
        lines.append(f"{t.strip()} — {s}")
    return lines


def _evolve_arxiv_queries() -> None:
    """Every 6 keyword-tracking rounds, ask LLM to generate fresh arXiv
    search queries from top-performing keywords. Store results for next scan."""
    from clients.cloud_client import chat
    from clients.cost_guard import BudgetExceededError

    top = store.top_keywords(limit=15, min_hits=2)
    if len(top) < 3:
        return  # not enough signal yet

    kw_list = ", ".join(f"{k['keyword']}({k['hits']}x)" for k in top)
    prompt = (
        f"Top keywords from recent interesting findings (scientist/engineer/doctor profile):\n{kw_list}\n\n"
        "Generate 3 arXiv search queries to find NEW relevant research papers. "
        "Each query must be a URL-encoded arXiv API query using 'all:' prefix and boolean operators "
        "(AND/OR). Return ONLY a JSON list of strings, no explanation."
    )
    try:
        resp = chat(user_message=prompt, reasoning_effort="low")
    except (BudgetExceededError, Exception) as e:
        _log.debug("[world_monitor] evolve skipped: %s", e)
        return

    try:
        start, end = resp.find("["), resp.rfind("]")
        if start == -1 or end == -1:
            return
        queries = json.loads(resp[start:end + 1])
        if isinstance(queries, list) and queries:
            import shlex
            encoded = [shlex.quote(q) for q in queries[:5] if isinstance(q, str)]
            store.set_evolved_queries(json.dumps(queries[:5]))
            _log.info("[world_monitor] evolved %d new arXiv queries", len(queries[:5]))
    except (json.JSONDecodeError, ValueError) as e:
        _log.debug("[world_monitor] evolve parse failed: %s", e)


def _judge_findings(raw_data: dict[str, str]) -> list[dict]:
    from clients.cloud_client import chat
    from clients.cost_guard import BudgetExceededError

    payload = json.dumps(raw_data, indent=2, default=str)
    if len(payload) > 12000:
        payload = payload[:12000] + "\n... [truncated]"

    try:
        resp = chat(
            user_message=f"Raw world data:\n\n{payload}",
            system_prompt=WORLD_MONITOR_SYSTEM_PROMPT,
            reasoning_effort="low",
        )
    except BudgetExceededError:
        _log.warning("[world_monitor] budget exceeded — surfacing raw sources instead")
        src_summary = "; ".join(f"{k}: {len(v)} chars" for k, v in raw_data.items())
        return [{"finding": f"World scan complete — {len(raw_data)} sources scanned ({src_summary}). Budget exceeded for AI analysis — raw data available.", "reason": "Budget exceeded, raw data available in logs"}]
    except Exception as e:
        _log.warning("[world_monitor] LLM judgment failed: %s", e)
        return []

    try:
        start, end = resp.find("["), resp.rfind("]")
        if start == -1 or end == -1:
            return []
        return json.loads(resp[start:end + 1])
    except (json.JSONDecodeError, ValueError) as e:
        _log.warning("[world_monitor] LLM response unparseable: %s", e)
        return []


def handle(instance: dict) -> dict:
    config = instance.get("config", {}) or {}
    poll_hours = int(config.get("poll_hours", 4))

    _log.info("[world_monitor] scanning global intelligence...")

    store.init()

    raw = _query_sources()
    if not raw:
        _log.info("[world_monitor] no data returned from any source")
        return {"status": "no_data"}

    _log.info("[world_monitor] got data from: %s", ", ".join(raw.keys()))

    for src, text in raw.items():
        store.save_raw_scan(src, text)

    findings = _judge_findings(raw)

    if not findings:
        _log.info("[world_monitor] nothing interesting to surface")
        for src in raw:
            store.record_source_scan(src, 0)
        store.cleanup_old_scans()
        return {"status": "no_findings", "sources": list(raw.keys())}

    new_count = 0
    deduped_count = 0
    for f in findings:
        finding_text = f.get("finding", "")
        reason = f.get("reason", "")
        if store.is_known(finding_text):
            deduped_count += 1
            continue

        new_count += 1
        store.mark_notified(finding_text, reason, ", ".join(raw.keys()))
        message = f"[World] {finding_text}\n\n{reason}" if reason else f"[World] {finding_text}"

        decide_and_notify(
            finding=message,
            source="world_monitor",
            fallback_alert=True,
            bypass_gate=True,
            level="info",
            call_phone=True,
        )

    # Track source hit rate
    for src in raw:
        findings_from_this_source = 0
        for f in findings:
            if f.get("finding", "") and store.is_known(f["finding"]):
                findings_from_this_source += 1
        store.record_source_scan(src, findings_from_this_source)

    # Evolve keywords — extract words from gate-passing findings, record hits
    _log.info("[world_monitor] extracting keywords from findings...")
    for f in findings:
        finding_text = f.get("finding", "")
        reason = f.get("reason", "")
        combined = f"{finding_text} {reason}"
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z-]{3,}", combined.lower())
                 if w not in ("this", "that", "with", "from", "what", "they", "been", "have", "into", "more", "about", "than", "then", "also", "some", "these", "their")]
        for w in words[:8]:
            store.record_keyword_hit(w)

    # Every 6 runs, evolve arXiv search queries using top keywords
    try:
        _evolve_arxiv_queries()
    except Exception as e:
        _log.warning("[world_monitor] query evolution failed: %s", e)

    _log.info("[world_monitor] %d new, %d deduped", new_count, deduped_count)
    store.cleanup_old_scans()

    return {
        "status": "delivered",
        "new_findings": new_count,
        "deduped": deduped_count,
    }
