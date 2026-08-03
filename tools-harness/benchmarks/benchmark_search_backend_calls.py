#!/usr/bin/env python3
"""
Compare local models on search backend routing via native Ollama tool calling.

Focus:
- choose the right tool among google_search / tech_search / brave_search
- build a usable query
- add freshness for Brave when appropriate

Usage:
    python3 tools-harness/benchmark_search_backend_calls.py

Environment:
    SEARCH_BACKEND_MODELS="granite3.3:2b,qwen3:4b"
    OLLAMA_URL="http://127.0.0.1:11434/api/chat"
"""

from __future__ import annotations

import json
import os
import time
import urllib.request


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
MODELS = [
    model.strip()
    for model in os.environ.get("SEARCH_BACKEND_MODELS", "granite3.3:2b,qwen3:4b").split(",")
    if model.strip()
]

SYSTEM = """You are a search router.
Choose exactly one tool call and nothing else.
Do not answer from memory.
Use:
- google_search for live sports, finance, weather
- tech_search for repos, library versions, release notes, technical docs
- brave_search for local places, science, current news, niche current topics
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "google_search",
            "description": "Google/SerpAPI search for live sports, finance, and weather.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "engine": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tech_search",
            "description": "Exa-based search for repos, docs, package versions, and technical content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brave_search",
            "description": "Brave search for local, science, and current-news queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "freshness": {"type": "string", "enum": ["pd", "pw", "pm"]},
                },
                "required": ["query"],
            },
        },
    },
]


def post_chat(payload: dict) -> dict:
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def ask_tool(model: str, prompt: str) -> tuple[dict, float]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "tools": TOOLS,
        "think": False,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 220},
    }
    started = time.time()
    raw = post_chat(payload)
    elapsed = time.time() - started
    return raw, elapsed


def first_tool_call(raw: dict) -> tuple[str | None, dict]:
    tool_calls = raw.get("message", {}).get("tool_calls", []) or []
    if not tool_calls:
        return None, {}
    fn = tool_calls[0].get("function", {})
    args = fn.get("arguments", {}) or {}
    if "function" in args and "arguments" in args:
        nested_name = args.get("function")
        nested_args = args.get("arguments", {}) or {}
        return nested_name or fn.get("name"), nested_args
    return fn.get("name"), args


def score_case(expected_tool: str, raw: dict, query_checks: list[str], freshness_expected: str | None = None) -> tuple[int, list[str]]:
    notes = []
    tool_name, args = first_tool_call(raw)
    if not tool_name:
        return 0, ["no_tool_call", raw.get("message", {}).get("content", "")[:200]]
    score = 2
    if tool_name == expected_tool:
        score += 4
    else:
        notes.append(f"tool={tool_name!r}")
    query = str(args.get("query", "")).lower()
    good = all(check in query for check in query_checks)
    if good:
        score += 3
    else:
        notes.append(f"query={args.get('query')!r}")
    if freshness_expected is not None:
        if args.get("freshness") == freshness_expected:
            score += 1
        else:
            notes.append(f"freshness={args.get('freshness')!r}")
    else:
        score += 1
    return score, notes


def main() -> int:
    tasks = [
        {
            "name": "sports_world_cup",
            "prompt": "When is the next World Cup going to start and what is the first match?",
            "expected_tool": "google_search",
            "query_checks": ["world cup", "first match"],
            "freshness": None,
        },
        {
            "name": "finance_btc",
            "prompt": "What's Bitcoin trading at right now in USD?",
            "expected_tool": "google_search",
            "query_checks": ["bitcoin", "usd"],
            "freshness": None,
        },
        {
            "name": "weather_sf",
            "prompt": "What's the weather in San Francisco today?",
            "expected_tool": "google_search",
            "query_checks": ["weather", "san francisco"],
            "freshness": None,
        },
        {
            "name": "tech_pydantic",
            "prompt": "What's the latest stable Pydantic version and release notes?",
            "expected_tool": "tech_search",
            "query_checks": ["pydantic", "latest", "release"],
            "freshness": None,
        },
        {
            "name": "local_coffee",
            "prompt": "Best coffee shops in Lisbon old town right now.",
            "expected_tool": "brave_search",
            "query_checks": ["coffee", "lisbon"],
            "freshness": "pw",
        },
        {
            "name": "science_starship",
            "prompt": "Latest SpaceX Starship test update.",
            "expected_tool": "brave_search",
            "query_checks": ["spacex", "starship"],
            "freshness": "pw",
        },
    ]

    overall = []
    for model in MODELS:
        print(f"MODEL {model}", flush=True)
        total = 0
        latencies = []
        for task in tasks:
            raw, elapsed = ask_tool(model, task["prompt"])
            score, notes = score_case(
                task["expected_tool"],
                raw,
                task["query_checks"],
                task["freshness"],
            )
            total += score
            latencies.append(elapsed)
            tool_name, args = first_tool_call(raw)
            print(
                json.dumps(
                    {
                        "task": task["name"],
                        "score": score,
                        "max": 10,
                        "seconds": round(elapsed, 2),
                        "tool": tool_name,
                        "args": args,
                        "notes": notes,
                        "content_preview": raw.get("message", {}).get("content", "")[:160],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        row = {
            "model": model,
            "score": total,
            "max": len(tasks) * 10,
            "pct": round(100 * total / (len(tasks) * 10), 1),
            "avg_seconds": round(sum(latencies) / len(latencies), 2),
        }
        overall.append(row)
        print("SUMMARY_ROW " + json.dumps(row, sort_keys=True), flush=True)

    print("FINAL_SUMMARY", flush=True)
    for row in sorted(overall, key=lambda item: (item["score"], -item["avg_seconds"]), reverse=True):
        print(json.dumps(row, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
