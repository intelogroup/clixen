"""
Scraper specialist subagent.

Mission: scrape web content — navigate pages, extract data, take screenshots.

Tools: browser_navigate, browser_snapshot, browser_click, browser_get_content,
       browser_screenshot, browser_type, browser_select, browser_run_js,
       crawl_url (crawl4ai), firecrawl_scrape (firecrawl-py)

Public API:
    run_scraper_specialist(query, model="gemma4", url=None, max_steps=8) -> ScraperResult
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import ollama
from pydantic import BaseModel, Field

from tools.registry import ALL_TOOLS, EXECUTORS, execute_tool, tools_with_tags

_log = logging.getLogger("scraper_specialist")

SCRAPER_TOOL_NAMES = tools_with_tags("spec_scraper")


def _scraper_tool_schemas() -> list[dict]:
    return [t for t in ALL_TOOLS if t["function"]["name"] in SCRAPER_TOOL_NAMES]


class ScraperResult(BaseModel):
    content: str = ""
    url: str = ""
    screenshot_path: str = ""
    tool_trace: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Built-in: crawl_url using crawl4ai
# ---------------------------------------------------------------------------

def _crawl_url(url: str) -> str:
    """Fetch and parse a URL using crawl4ai (returns markdown)."""
    try:
        from crawl4ai import WebCrawler
        crawler = WebCrawler()
        result = crawler.run(url=url)
        if result and result.markdown:
            return result.markdown[:10000]
        return f"No content extracted from {url}"
    except ImportError:
        pass

    # Fallback: requests + html2text
    try:
        import requests
        import html2text
        h = html2text.HTML2Text()
        h.ignore_links = False
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; g4l-scraper/1.0)"
        })
        resp.raise_for_status()
        return h.handle(resp.text)[:10000]
    except Exception as e:
        return f"crawl_url failed: {type(e).__name__}: {e}"


# Register built-in
_BUILTIN_SCRAPER = {
    "crawl_url": lambda args: _crawl_url(args["url"]),
}

for _name, _fn in _BUILTIN_SCRAPER.items():
    if _name not in EXECUTORS:
        EXECUTORS[_name] = _fn

_CRAWL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "crawl_url",
        "description": (
            "Fetch and extract the main text content from a URL as clean markdown. "
            "Uses crawl4ai for AI-friendly scraping. Returns up to 10k chars."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to crawl"},
            },
            "required": ["url"],
        },
    },
}

for _schema in [_CRAWL_SCHEMA]:
    _name = _schema["function"]["name"]
    if not any(t["function"]["name"] == _name for t in ALL_TOOLS):
        ALL_TOOLS.append(_schema)
    if _name not in EXECUTORS:
        EXECUTORS[_name] = _BUILTIN_SCRAPER[_name]


# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """You are WEB SCRAPER. Your job: navigate websites, extract content, and take screenshots.

TOOLS:
  browser_navigate(url)           — navigate to a URL
  browser_snapshot(selector)      — get a text snapshot of the page
  browser_get_content(selector)   — extract text content from an element
  browser_click(selector)         — click an element
  browser_type(selector, text)    — type text into an input
  browser_select(selector, value) — select a dropdown option
  browser_screenshot(path)        — take a full-page screenshot
  browser_run_js(script)          — run JavaScript in the page
  browser_wait(selector)          — wait for an element to appear
  crawl_url(url)                  — quick scrape a URL (no browser needed)

RULES:
- Use crawl_url for quick content extraction (no browser overhead).
- Use browser_navigate + browser_snapshot for interactive pages.
- Always start with the simplest tool that gets the job done.
- Stop after extracting the requested content.
- Stop after {max_steps} calls."""


def _build_system_prompt(max_steps: int) -> str:
    return _SYSTEM_PROMPT.format(max_steps=max_steps)


def run_scraper_specialist(
    query: str,
    model: str = "gemma4:12b-mlx",
    url: str | None = None,
    max_steps: int = 8,
    timeout_s: float = 300.0,
) -> ScraperResult:
    t0 = time.time()

    # ── Fast path: URL in query → crawl directly ──
    url_match = re.search(r"https?://[^\s\"']+", query)
    if not url and url_match:
        url = url_match.group(0)

    if url:
        _log.info("[scraper] fast path: crawl %s", url)
        content = _crawl_url(url)
        return ScraperResult(
            content=content,
            url=url,
            tool_trace=["crawl_url"],
            elapsed_s=time.time() - t0,
        )

    # ── ReAct loop ──
    tools = _scraper_tool_schemas()
    tool_names = {t["function"]["name"] for t in tools}
    system = _build_system_prompt(max_steps)
    from tools.memory_tools import mem_block_for
    _mem = mem_block_for(query)
    if _mem:
        system = _mem + "\n" + system

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ]

    tool_trace: list[str] = []
    content: str = ""
    screenshot: str = ""

    for step in range(max_steps):
        if time.time() - t0 > timeout_s:
            return ScraperResult(
                content=content,
                screenshot_path=screenshot,
                tool_trace=tool_trace,
                elapsed_s=time.time() - t0,
                error="timeout",
            )

        try:
            resp = ollama.chat(
                model=model,
                messages=messages,
                tools=tools,
                options={"temperature": 0.1, "num_ctx": 8192},
            )
        except Exception as e:
            return ScraperResult(
                tool_trace=tool_trace,
                elapsed_s=time.time() - t0,
                error=f"ollama error: {e}",
            )

        msg = resp.get("message", {})
        resp_content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            content = resp_content
            break

        for tc in tool_calls:
            fn = tc.get("function", tc) if isinstance(tc, dict) else getattr(tc, "function", None)
            if not fn:
                continue
            name = fn.get("name", "") or getattr(fn, "name", "")
            args_raw = fn.get("arguments", {}) or getattr(fn, "arguments", {})
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = dict(args_raw) if args_raw else {}

            if name not in tool_names:
                continue

            tool_trace.append(name)

            if name == "browser_navigate" and not url:
                url = str(args.get("url", ""))

            try:
                r = execute_tool(name, args)
                r_str = str(r) if r else ""
            except Exception as e:
                r_str = f"Error: {type(e).__name__}: {e}"

            if name in {"browser_get_content", "browser_snapshot"} and r_str:
                content = r_str
            if name == "browser_screenshot":
                screenshot = str(args.get("path", ""))

            messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
            messages.append({"role": "tool", "content": r_str})

    return ScraperResult(
        content=content,
        url=url or "",
        screenshot_path=screenshot,
        tool_trace=tool_trace,
        elapsed_s=time.time() - t0,
    )
