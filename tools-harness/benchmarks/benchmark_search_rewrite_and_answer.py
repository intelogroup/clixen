#!/usr/bin/env python3
"""
Benchmark mini models for deterministic-search pipelines:
1. backend is chosen by rules
2. model rewrites the user query for that backend
3. local search executor runs
4. model writes the final concise answer

This avoids brittle native tool-calling and measures the two jobs that should
actually belong to the model: query shaping and user-facing summarization.

Usage:
    python3 tools-harness/benchmark_search_rewrite_and_answer.py
"""

from __future__ import annotations

import json
import os
import re
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

from tools.registry import EXECUTORS  # noqa: E402


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
MODELS = [
    model.strip()
    for model in os.environ.get(
        "SEARCH_REWRITE_MODELS",
        "qwen3.5:4b,qwen3:4b,mistral-nemo",
    ).split(",")
    if model.strip()
]

REWRITE_SYSTEM = """You rewrite search queries for a fixed backend.
Return exactly one JSON object. No markdown.
Do not answer the question itself.
"""

ANSWER_SYSTEM = """You answer users using fetched search results only.
Return exactly one JSON object. No markdown.
Do not invent facts.
Be concise and human.
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


def ask_json(model: str, system: str, prompt: str, schema: dict) -> tuple[dict | None, str, float]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "think": False,
        "format": schema,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 260},
    }
    started = time.time()
    raw = post_chat(payload)
    elapsed = time.time() - started
    content = raw.get("message", {}).get("content", "")
    try:
        return json.loads(content), content, elapsed
    except json.JSONDecodeError:
        return None, content, elapsed


def run_search(tool_name: str, args: dict) -> str:
    return EXECUTORS[tool_name](args)


def score_rewrite(expected_terms: list[str], rewrite: dict | None) -> tuple[int, list[str]]:
    notes = []
    if not isinstance(rewrite, dict):
        return 0, ["invalid_json"]
    score = 2
    query = str(rewrite.get("query", "")).lower()
    if all(term in query for term in expected_terms):
        score += 5
    else:
        notes.append(f"query={rewrite.get('query')!r}")
    if rewrite.get("max_results") in {3, 4, 5}:
        score += 1
    else:
        notes.append(f"max_results={rewrite.get('max_results')!r}")
    if "notes" in rewrite and isinstance(rewrite["notes"], str) and len(rewrite["notes"]) < 120:
        score += 2
    else:
        notes.append("notes_missing")
    return score, notes


def score_answer(case: dict, answer: dict | None) -> tuple[int, list[str]]:
    notes = []
    if not isinstance(answer, dict):
        return 0, ["invalid_json"]
    score = 2
    message = str(answer.get("message", ""))
    lowered = message.lower()
    if len(message) < 20:
        notes.append("answer_too_short")
        score -= 2
    elif len(message) <= case["max_chars"]:
        score += 2
    else:
        notes.append(f"len={len(message)}")
    hit_count = 0
    for term in case["answer_terms"]:
        if term in lowered:
            hit_count += 1
    score += min(4, hit_count)
    if hit_count < min(2, len(case["answer_terms"])):
        notes.append(f"missing_terms={case['answer_terms']}")
    if answer.get("grounded") is True:
        score += 2
    else:
        notes.append(f"grounded={answer.get('grounded')!r}")
    # Hallucination penalty: forbidden terms that must not appear in the answer
    for bad in case.get("forbidden_terms", []):
        if bad in lowered:
            score -= 2
            notes.append(f"hallucinated={bad!r}")
    return max(0, score), notes


def main() -> int:
    cases = [
        # --- google_search ---
        {
            "name": "sports_world_cup",
            "category": "sports",
            "tool": "google_search",
            "tool_args": {"engine": "google"},
            "user": "when is the next world cup gonna start and what is the first match",
            "rewrite_terms": ["world cup", "first match"],
            "answer_terms": ["world cup", "first", "match"],
            "max_chars": 180,
            # granite hallucinated the 2022 edition on prior runs
            "forbidden_terms": ["2022", "qatar"],
        },
        {
            "name": "sports_champions",
            "category": "sports",
            "tool": "google_search",
            "tool_args": {"engine": "google"},
            "user": "who won the champions league most recently",
            "rewrite_terms": ["champions league"],
            "answer_terms": ["champions league"],
            "max_chars": 200,
            "forbidden_terms": [],
        },
        {
            "name": "finance_gold",
            "category": "finance",
            "tool": "google_search",
            "tool_args": {"engine": "google"},
            "user": "what is the current price of gold per ounce today",
            "rewrite_terms": ["gold", "price"],
            "answer_terms": ["gold", "price"],
            "max_chars": 160,
            "forbidden_terms": [],
        },
        # --- tech_search ---
        {
            "name": "tech_pydantic",
            "category": "tech",
            "tool": "tech_search",
            "tool_args": {"max_results": 3},
            "user": "what's the latest stable pydantic version and release notes",
            "rewrite_terms": ["pydantic", "latest", "release"],
            "answer_terms": ["pydantic", "release"],
            "max_chars": 180,
            "forbidden_terms": [],
        },
        {
            "name": "tech_fastapi_background",
            "category": "tech",
            "tool": "tech_search",
            "tool_args": {"max_results": 3},
            "user": "how do I run a background task in fastapi without blocking the response",
            "rewrite_terms": ["fastapi", "background"],
            "answer_terms": ["fastapi", "background"],
            "max_chars": 280,
            "forbidden_terms": [],
        },
        {
            "name": "tech_ollama_version",
            "category": "tech",
            "tool": "tech_search",
            "tool_args": {"max_results": 3},
            "user": "what is the latest ollama release and what changed in it",
            "rewrite_terms": ["ollama", "release"],
            "answer_terms": ["ollama"],
            "max_chars": 220,
            "forbidden_terms": [],
        },
        # --- brave_search ---
        {
            "name": "local_coffee",
            "category": "local",
            "tool": "brave_search",
            "tool_args": {"freshness": "pm"},
            "user": "best coffee shops in lisbon old town right now",
            "rewrite_terms": ["coffee", "lisbon"],
            "answer_terms": ["lisbon", "coffee"],
            "max_chars": 220,
            "forbidden_terms": [],
        },
        {
            "name": "local_sushi",
            "category": "local",
            "tool": "brave_search",
            "tool_args": {"freshness": "pm"},
            "user": "best sushi restaurants near the eiffel tower in paris",
            "rewrite_terms": ["sushi", "paris"],
            "answer_terms": ["paris", "sushi"],
            "max_chars": 250,
            "forbidden_terms": [],
        },
        {
            "name": "local_constraint",
            "category": "local",
            "tool": "brave_search",
            "tool_args": {"freshness": "pm"},
            "user": "rooftop bars in barcelona that are open late at night",
            "rewrite_terms": ["rooftop", "barcelona"],
            "answer_terms": ["barcelona"],
            "max_chars": 250,
            "forbidden_terms": [],
        },
        {
            "name": "news_ai_week",
            "category": "news",
            "tool": "brave_search",
            "tool_args": {"freshness": "pw"},
            "user": "what are the most important AI news stories this week",
            "rewrite_terms": ["ai", "news"],
            "answer_terms": ["ai"],
            "max_chars": 300,
            "forbidden_terms": [],
        },
    ]

    rewrite_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"},
            "notes": {"type": "string"},
        },
        "required": ["query", "max_results", "notes"],
    }
    answer_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "grounded": {"type": "boolean"},
        },
        "required": ["message", "grounded"],
    }

    categories = sorted({c["category"] for c in cases})
    overall = []
    for model in MODELS:
        print(f"\nMODEL {model}", flush=True)
        total = 0
        latencies = []
        cat_scores: dict[str, list[int]] = {cat: [] for cat in categories}
        for case in cases:
            rewrite_prompt = (
                f"Backend is fixed to `{case['tool']}`.\n"
                f"Rewrite this user request into the best search query for that backend.\n"
                f"User request: {case['user']}"
            )
            rewrite, rewrite_raw, t1 = ask_json(model, REWRITE_SYSTEM, rewrite_prompt, rewrite_schema)
            rewrite_score, rewrite_notes = score_rewrite(case["rewrite_terms"], rewrite)

            query = rewrite.get("query") if isinstance(rewrite, dict) else case["user"]
            tool_args = dict(case["tool_args"])
            tool_args["query"] = query
            search_output = run_search(case["tool"], tool_args)

            answer_prompt = (
                f"User request: {case['user']}\n\n"
                f"Search backend: {case['tool']}\n"
                f"Search results:\n{search_output}\n\n"
                "Write one concise answer for the user based only on those results."
            )
            answer, answer_raw, t2 = ask_json(model, ANSWER_SYSTEM, answer_prompt, answer_schema)
            answer_score, answer_notes = score_answer(case, answer)

            total_case = rewrite_score + answer_score
            total += total_case
            latencies.append(t1 + t2)
            cat_scores[case["category"]].append(total_case)
            print(
                json.dumps(
                    {
                        "task": case["name"],
                        "category": case["category"],
                        "rewrite_score": rewrite_score,
                        "answer_score": answer_score,
                        "score": total_case,
                        "max": 20,
                        "seconds": round(t1 + t2, 2),
                        "rewrite_notes": rewrite_notes,
                        "answer_notes": answer_notes,
                        "query": query,
                        "answer_preview": answer_raw[:200],
                        "search_preview": search_output[:220],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        cat_summary = {
            cat: {
                "score": sum(scores),
                "max": len(scores) * 20,
                "pct": round(100 * sum(scores) / (len(scores) * 20), 1) if scores else 0,
            }
            for cat, scores in cat_scores.items()
            if scores
        }
        row = {
            "model": model,
            "score": total,
            "max": len(cases) * 20,
            "pct": round(100 * total / (len(cases) * 20), 1),
            "avg_seconds": round(sum(latencies) / len(latencies), 2),
            "by_category": cat_summary,
        }
        overall.append(row)
        print("SUMMARY_ROW " + json.dumps(row, sort_keys=True), flush=True)

    print("\nFINAL_SUMMARY", flush=True)
    for row in sorted(overall, key=lambda item: (item["score"], -item["avg_seconds"]), reverse=True):
        print(json.dumps(row, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
