#!/usr/bin/env python3
"""
Hard-question evaluation pack for the deterministic search stack.

Runs adversarial live-ish queries through `run_search_graph()` with fixed
intent/domain labels from `hard_search_cases.json`, then scores the answer for:
  - clarification behavior on underspecified queries
  - required term coverage
  - forbidden phrase leakage
  - rough answer quality / grounding heuristics
  - latency

Usage:
    python3 tools-harness/benchmark_search_hard.py
    python3 tools-harness/benchmark_search_hard.py --limit 5
    python3 tools-harness/benchmark_search_hard.py --category finance
    python3 tools-harness/benchmark_search_hard.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")
load_dotenv(ROOT.parent / ".env.local")

from agents.search_graph import run_search_graph  # noqa: E402


CASES_PATH = ROOT / "hard_search_cases.json"


def load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text())


def score_case(case: dict, answer: str, elapsed_s: float) -> tuple[int, list[str]]:
    notes: list[str] = []
    lowered = answer.lower()
    score = 0

    if case.get("expect_clarification"):
        if "?" in answer:
            score += 6
        else:
            notes.append("missing_clarification")
        for term in case.get("must_include_any", []):
            if term.lower() in lowered:
                score += 1
                break
        if len(answer) < 220:
            score += 1
        else:
            notes.append("clarification_too_long")
        return min(score, 10), notes

    must_terms = case.get("must_include_any", [])
    should_terms = case.get("should_include_any", [])
    forbidden_terms = case.get("forbidden_any", [])

    must_hits = [term for term in must_terms if term.lower() in lowered]
    should_hits = [term for term in should_terms if term.lower() in lowered]
    forbidden_hits = [term for term in forbidden_terms if term.lower() in lowered]

    if must_hits:
        score += 4
    else:
        notes.append(f"missing_required_any={must_terms}")

    score += min(3, len(should_hits))
    if should_terms and not should_hits:
        notes.append(f"missing_should_any={should_terms}")

    if forbidden_hits:
        score -= min(4, len(forbidden_hits))
        notes.append(f"forbidden_hits={forbidden_hits}")
    else:
        score += 1

    if "[search_failed" in answer.lower():
        score -= 3
        notes.append("search_failed")

    if len(answer.strip()) >= 20:
        score += 1
    else:
        notes.append("too_short")

    if len(answer) <= 320:
        score += 1
    else:
        notes.append(f"too_long={len(answer)}")

    if elapsed_s <= 12:
        score += 0
    else:
        notes.append(f"slow={elapsed_s:.1f}s")

    return max(0, min(score, 10)), notes


def run_one(case: dict) -> dict:
    started = time.time()
    answer = run_search_graph(
        query=case["question"],
        intent=case["intent"],
        domain=case["domain"],
    )
    elapsed = time.time() - started
    score, notes = score_case(case, answer, elapsed)
    return {
        "id": case["id"],
        "category": case["category"],
        "intent": case["intent"],
        "domain": case["domain"],
        "question": case["question"],
        "score": score,
        "max_score": 10,
        "seconds": round(elapsed, 2),
        "notes": notes,
        "answer_preview": answer[:280].replace("\n", " "),
    }


def select_cases(cases: list[dict], category: str, limit: int | None) -> list[dict]:
    selected = [case for case in cases if not category or case["category"] == category]
    if limit is not None:
        selected = selected[:limit]
    return selected


def print_human(results: list[dict]) -> None:
    for row in results:
        print(
            json.dumps(
                {
                    "id": row["id"],
                    "category": row["category"],
                    "score": row["score"],
                    "max_score": row["max_score"],
                    "seconds": row["seconds"],
                    "notes": row["notes"],
                    "answer_preview": row["answer_preview"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    scores = [row["score"] for row in results]
    latencies = [row["seconds"] for row in results]
    summary = {
        "cases": len(results),
        "total_score": sum(scores),
        "max_score": len(results) * 10,
        "pct": round(100 * sum(scores) / (len(results) * 10), 1) if results else 0.0,
        "avg_score": round(statistics.mean(scores), 2) if scores else 0.0,
        "avg_seconds": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "p95_seconds": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 2) if latencies else 0.0,
    }
    print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default="", help="Filter cases by category")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N selected cases")
    parser.add_argument("--json", action="store_true", help="Print one final JSON object instead of per-case lines")
    args = parser.parse_args()

    cases = select_cases(load_cases(), args.category, args.limit)
    results = [run_one(case) for case in cases]

    if args.json:
        print(json.dumps({"results": results}, indent=2, sort_keys=True))
    else:
        print_human(results)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
