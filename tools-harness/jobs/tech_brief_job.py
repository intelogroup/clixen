"""
Tech Briefing Job
=================
Fetches HN + TechCrunch headlines via Exa -> Tavily -> SearXNG(Brave/DDG)
fallback chain (no browser), compiles plain-text tech briefing via the cloud
default model, emails to the configured TECH_BRIEF_EMAIL. Runs 7AM/6PM via scheduler.

Run manually:
    cd tools-harness && python -m jobs.tech_brief_job
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from clients import cloud_client
from clients.cloud_client import raw_completion
from tools.registry import EXECUTORS
from tools.searxng_search import execute as _searxng_execute
from store import tech_brief_store

TARGET_EMAIL = os.environ.get("TECH_BRIEF_EMAIL", "").strip() or os.environ.get("CLIXEN_EMAIL", "")


def _headlines_via_exa(query: str) -> list[str]:
    if not os.environ.get("EXA_API_KEY"):
        return []
    try:
        from exa_py import Exa
        client = Exa(api_key=os.environ["EXA_API_KEY"])
        resp = client.search(query, num_results=15, type="auto")
        return [r.title for r in resp.results if r.title]
    except Exception as e:
        print(f"[tech_brief] exa failed: {e}")
        return []


def _headlines_via_tavily(query: str) -> list[str]:
    if not os.environ.get("TAVILY_API_KEY"):
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
        resp = client.search(query=query, max_results=15, search_depth="basic")
        return [r.get("title", "") for r in resp.get("results", []) if r.get("title")]
    except Exception as e:
        print(f"[tech_brief] tavily failed: {e}")
        return []


def _headlines_via_searxng(query: str) -> list[str]:
    try:
        result = _searxng_execute(query, categories="news", max_results=15)
        if result.ok:
            return [item.title for item in result.items if item.title]
    except Exception as e:
        print(f"[tech_brief] searxng failed: {e}")
    return []


def _fetch_headlines(query: str) -> str:
    """Exa -> Tavily -> SearXNG(Brave/DDG aggregated), first backend with results wins."""
    for fn in (_headlines_via_exa, _headlines_via_tavily, _headlines_via_searxng):
        lines = fn(query)
        if lines:
            return "\n".join(lines[:20])
    return ""


def _fetch_hn() -> str:
    return _fetch_headlines("site:news.ycombinator.com top stories today")


def _fetch_tc() -> str:
    return _fetch_headlines("site:techcrunch.com latest tech news today")


def _compile_and_send(hn_text: str, tc_text: str) -> str:
    """Use the cloud default model to compile a plain-text briefing, then email it."""
    from datetime import datetime
    now = datetime.now().astimezone()
    date_str = now.strftime("%B %d, %Y")
    period = "AM" if now.hour < 12 else "PM"

    messages = [
        {"role": "system", "content": (
            "You write plain text emails. NO markdown, NO asterisks for bold, NO hashtags. "
            "Use CAPS for section headers, dashes for lists, blank lines between sections."
        )},
        {"role": "user", "content": (
            f"Compile today's tech news into a plain-text briefing and email it to {TARGET_EMAIL} "
            f"with subject 'Tech Briefing - {date_str} ({period})'.\n\n"
            f"Format the body as plain text: CAPS headers, dashes for lists, "
            f"group related news under sections. NO star characters at all.\n\n"
            f"HACKER NEWS:\n{hn_text}\n\nTECHCRUNCH:\n{tc_text}"
        )},
    ]

    # Cloud-first (2026-07-31): local gemma4:12b-mlx is not reliably loaded in
    # this cloud-first env — the old hardcoded model 404'd every run. Cloud
    # default + built-in OpenAI fallback (raw_completion) instead.
    choice = raw_completion(
        model=cloud_client.DEFAULT_CLOUD_MODEL,
        messages=messages,
        tools=[],
        temperature=0.7,
        bypass_budget=True,
    )
    content = (choice.message.content or "").replace("**", "").replace("*", "")
    if not content:
        return "ERROR: No response from model"
    return EXECUTORS["send_email"]({
        "to": TARGET_EMAIL,
        "subject": f"Tech Briefing - {date_str} ({period})",
        "body": content,
    })


def run_briefing() -> str:
    """Fetch news, compile, and email."""
    import logging
    log = logging.getLogger("tech_brief")
    log.info("tech_brief: fetching HN...")
    hn = _fetch_hn()
    log.info("tech_brief: fetching TechCrunch...")
    tc = _fetch_tc()

    hn_lines = tech_brief_store.dedupe_lines("hn", hn.split("\n"))
    tc_lines = tech_brief_store.dedupe_lines("tc", tc.split("\n"))
    if not hn_lines and not tc_lines:
        log.info("tech_brief: nothing new since last run, skipping send")
        return "SKIPPED: no new headlines"

    log.info("tech_brief: compiling and sending...")
    result = _compile_and_send("\n".join(hn_lines), "\n".join(tc_lines))
    log.info("tech_brief: %s", result)
    return result


def run_as_job(params: dict, job_id: str) -> None:
    """Entry point called by the worker."""
    import jobs.job_queue as jq
    jq.init()
    jq.checkpoint(job_id, "started")
    run_briefing()


if __name__ == "__main__":
    result = run_briefing()
    print(result)
