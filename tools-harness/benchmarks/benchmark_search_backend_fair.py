#!/usr/bin/env python3
"""
Docs-aligned search backend benchmark for Granite vs Qwen profiles.

Fairness adjustments:
- Uses the exact local tool schemas from tools.registry
- Qwen no_think profile uses /no_think and Qwen-recommended non-thinking sampling
- Qwen think profile uses thinking-enabled sampling from Qwen docs
- Granite uses temperature 0 per IBM Granite docs

Usage:
    python3 tools-harness/benchmark_search_backend_fair.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")
load_dotenv(ROOT.parent / ".env.local")

from tools.registry import ALL_TOOLS  # noqa: E402


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")

TOOL_NAMES = {"google_search", "brave_search", "tech_search"}
TOOLS = [tool for tool in ALL_TOOLS if tool["function"]["name"] in TOOL_NAMES]

PROFILES = [
    {
        "name": "granite3.3:2b",
        "model": "granite3.3:2b",
        "think": False,
        "options": {"temperature": 0, "num_predict": 220},
        "prepend": "",
    },
    {
        "name": "qwen3:4b/no_think",
        "model": "qwen3:4b",
        "think": False,
        "options": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "num_predict": 220},
        "prepend": "/no_think\n",
    },
    {
        "name": "qwen3:4b/think",
        "model": "qwen3:4b",
        "think": True,
        "options": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "num_predict": 220},
        "prepend": "",
    },
]

SYSTEM = """You are a search router inside a tool-using assistant.
Call exactly one tool.
Do not answer from memory.
Choose the most appropriate tool based on the tool descriptions themselves.
"""


def post_chat(payload: dict) -> dict:
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def ask_tool(profile: dict, prompt: str) -> tuple[dict, float]:
    payload = {
        "model": profile["model"],
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": profile["prepend"] + prompt},
        ],
        "tools": TOOLS,
        "think": profile["think"],
        "stream": False,
        "options": profile["options"],
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
        return args.get("function") or fn.get("name"), args.get("arguments", {}) or {}
    return fn.get("name"), args


def score_case(expected_tool: str, raw: dict, query_checks: list[str], freshness_expected: str | None = None) -> tuple[int, list[str]]:
    notes = []
    tool_name, args = first_tool_call(raw)
    if not tool_name:
        return 0, ["no_tool_call", raw.get("message", {}).get("content", "")[:160], raw.get("message", {}).get("thinking", "")[:160]]
    score = 2
    if tool_name == expected_tool:
        score += 4
    else:
        notes.append(f"tool={tool_name!r}")
    query = str(args.get("query", "")).lower()
    if all(check in query for check in query_checks):
        score += 3
    else:
        notes.append(f"query={args.get('query')!r}")
    if freshness_expected is None:
        score += 1
    elif args.get("freshness") == freshness_expected:
        score += 1
    else:
        notes.append(f"freshness={args.get('freshness')!r}")
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
            "freshness": "pm",
        },
        {
            "name": "science_starship",
            "prompt": "Latest SpaceX Starship test update.",
            "expected_tool": "brave_search",
            "query_checks": ["spacex", "starship"],
            "freshness": "pm",
        },
    ]

    overall = []
    for profile in PROFILES:
        print(f"PROFILE {profile['name']}", flush=True)
        total = 0
        latencies = []
        for task in tasks:
            raw, elapsed = ask_tool(profile, task["prompt"])
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
                        "thinking_preview": raw.get("message", {}).get("thinking", "")[:120],
                        "content_preview": raw.get("message", {}).get("content", "")[:120],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        row = {
            "profile": profile["name"],
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
