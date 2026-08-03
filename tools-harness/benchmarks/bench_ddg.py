"""
DDG live benchmark — runs hard_search_cases.json directly against DDG.
Measures: result relevance (must/should/forbidden), latency, failure rate.

Usage:
    python bench_ddg.py [--limit N] [--delay SECONDS] [--category CAT]

Rate limit note: DDG throttles aggressively. --delay 1.5 (default) is safe.
Reduce with --delay 0.5 if you have a fresh IP. Increase to 3 if seeing failures.
"""

import argparse
import json
import sys
import time
import os
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(__file__))

from tools.ddg_search import execute as ddg_execute


@dataclass
class EvalResult:
    case_id: str
    category: str
    intent: str
    question: str
    ok: bool
    error: str = ""
    must_pass: bool = False
    must_hit: list[str] = field(default_factory=list)
    must_miss: list[str] = field(default_factory=list)
    should_score: float = 0.0
    should_hit: list[str] = field(default_factory=list)
    forbidden_hit: list[str] = field(default_factory=list)
    latency_ms: int = 0
    snippets: list[str] = field(default_factory=list)
    result_count: int = 0
    skipped: bool = False


def _text_contains_any(text: str, terms: list[str]) -> tuple[bool, list[str]]:
    text_lower = text.lower()
    hits = [t for t in terms if t.lower() in text_lower]
    return bool(hits), hits


def eval_case(case: dict, delay: float) -> EvalResult:
    cid = case["id"]
    result = EvalResult(
        case_id=cid,
        category=case.get("category", ""),
        intent=case.get("intent", ""),
        question=case["question"],
        ok=False,
    )

    # Skip clarification-only cases — DDG can't answer these
    if case.get("expect_clarification"):
        result.skipped = True
        return result

    t0 = time.time()
    sr = ddg_execute(
        query=case["question"],
        max_results=3,
        intent=case.get("intent", ""),
        domain=case.get("domain", ""),
    )
    result.latency_ms = int((time.time() - t0) * 1000)

    time.sleep(delay)

    if not sr.ok:
        result.error = sr.error or "no results"
        return result

    result.ok = True
    result.result_count = len(sr.items or [])

    # Build combined text from all results
    # sr.items can be SearchSnippet objects or plain dicts
    def _get(item, key):
        return getattr(item, key, None) or (item.get(key, "") if isinstance(item, dict) else "") or ""

    combined = " ".join(
        f"{_get(item,'title')} {_get(item,'snippet')}"
        for item in (sr.items or [])
    ).lower()
    result.snippets = [
        f"{_get(item,'title')} | {_get(item,'snippet')[:120]}"
        for item in (sr.items or [])
    ]

    # must_include_any
    must_terms = case.get("must_include_any", [])
    if must_terms:
        found, hits = _text_contains_any(combined, must_terms)
        result.must_pass = found
        result.must_hit = hits
        result.must_miss = [t for t in must_terms if t.lower() not in combined]
    else:
        result.must_pass = True  # No constraint = pass

    # should_include_any
    should_terms = case.get("should_include_any", [])
    if should_terms:
        _, hits = _text_contains_any(combined, should_terms)
        result.should_hit = hits
        result.should_score = len(hits) / len(should_terms)

    # forbidden_any
    forbidden_terms = case.get("forbidden_any", [])
    if forbidden_terms:
        _, hits = _text_contains_any(combined, forbidden_terms)
        result.forbidden_hit = hits

    return result


def print_report(results: list[EvalResult]) -> None:
    active = [r for r in results if not r.skipped]
    skipped = [r for r in results if r.skipped]
    failed_fetch = [r for r in active if not r.ok]
    fetched = [r for r in active if r.ok]
    must_pass = [r for r in fetched if r.must_pass]
    must_fail = [r for r in fetched if not r.must_pass]
    forbidden_violations = [r for r in fetched if r.forbidden_hit]

    print("\n" + "=" * 72)
    print("DDG BENCHMARK REPORT")
    print("=" * 72)
    print(f"Total cases:          {len(results)}")
    print(f"Skipped (clarify):    {len(skipped)}")
    print(f"Ran:                  {len(active)}")
    print(f"Fetch failures:       {len(failed_fetch)} ({100*len(failed_fetch)/max(1,len(active)):.1f}%)")
    print(f"Results returned:     {len(fetched)}")
    print(f"Must-terms pass:      {len(must_pass)}/{len(fetched)} ({100*len(must_pass)/max(1,len(fetched)):.1f}%)")
    print(f"Forbidden violations: {len(forbidden_violations)}")

    if fetched:
        avg_should = sum(r.should_score for r in fetched) / len(fetched)
        avg_lat = sum(r.latency_ms for r in fetched) / len(fetched)
        p95_lat = sorted(r.latency_ms for r in fetched)[int(len(fetched) * 0.95)]
        avg_results = sum(r.result_count for r in fetched) / len(fetched)
        print(f"Should-score (avg):   {avg_should:.2f}")
        print(f"Avg results/query:    {avg_results:.1f}")
        print(f"Latency avg/p95:      {avg_lat:.0f}ms / {p95_lat}ms")

    # By category
    print("\n--- By category ---")
    cats: dict[str, dict] = {}
    for r in fetched:
        c = r.category or "unknown"
        cats.setdefault(c, {"total": 0, "must_pass": 0, "should": []})
        cats[c]["total"] += 1
        if r.must_pass:
            cats[c]["must_pass"] += 1
        cats[c]["should"].append(r.should_score)
    for cat, d in sorted(cats.items()):
        must_pct = 100 * d["must_pass"] / d["total"]
        avg_s = sum(d["should"]) / len(d["should"]) if d["should"] else 0
        print(f"  {cat:<30} must={must_pct:5.1f}%  should={avg_s:.2f}  n={d['total']}")

    # Fetch failures
    if failed_fetch:
        print("\n--- FETCH FAILURES ---")
        for r in failed_fetch:
            print(f"  [{r.case_id}] {r.error}")

    # Must-term failures
    if must_fail:
        print("\n--- MUST-TERM FAILURES ---")
        for r in must_fail:
            print(f"  [{r.case_id}]")
            print(f"    Q: {r.question[:80]}")
            print(f"    Missing: {r.must_miss}")
            if r.snippets:
                print(f"    Got: {r.snippets[0][:100]}")

    # Forbidden violations
    if forbidden_violations:
        print("\n--- FORBIDDEN TERM VIOLATIONS ---")
        for r in forbidden_violations:
            print(f"  [{r.case_id}] hit: {r.forbidden_hit}")

    # Best should-scores
    top5 = sorted(fetched, key=lambda r: r.should_score, reverse=True)[:5]
    print("\n--- TOP 5 (should-score) ---")
    for r in top5:
        print(f"  [{r.case_id}] should={r.should_score:.2f} lat={r.latency_ms}ms hit={r.should_hit}")

    # Worst should-scores (excluding clarification-expected)
    bot5 = sorted(
        [r for r in fetched if r.must_pass],
        key=lambda r: r.should_score
    )[:5]
    print("\n--- BOTTOM 5 (must pass, lowest should-score) ---")
    for r in bot5:
        print(f"  [{r.case_id}] should={r.should_score:.2f} lat={r.latency_ms}ms")
        if r.snippets:
            print(f"    {r.snippets[0][:100]}")

    print("\n" + "=" * 72)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Max cases to run (0=all)")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests (seconds)")
    parser.add_argument("--category", type=str, default="", help="Filter by category")
    parser.add_argument("--json-out", type=str, default="", help="Write raw results to JSON file")
    args = parser.parse_args()

    cases_path = os.path.join(os.path.dirname(__file__), "hard_search_cases.json")
    with open(cases_path) as f:
        cases = json.load(f)

    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]
        print(f"Filtered to category '{args.category}': {len(cases)} cases")

    if args.limit:
        cases = cases[: args.limit]
        print(f"Limited to {len(cases)} cases")

    print(f"Running {len(cases)} cases with {args.delay}s delay between requests...")
    results: list[EvalResult] = []

    for i, case in enumerate(cases, 1):
        cid = case["id"]
        skipping = case.get("expect_clarification", False)
        tag = "SKIP" if skipping else "RUN "
        print(f"  [{i:3d}/{len(cases)}] {tag} {cid}", end="", flush=True)
        r = eval_case(case, delay=args.delay if not skipping else 0)
        results.append(r)

        if r.skipped:
            print(" (clarification case)")
        elif not r.ok:
            print(f" FAIL {r.latency_ms}ms — {r.error}")
        else:
            must_sym = "✓" if r.must_pass else "✗"
            print(f" {must_sym} {r.latency_ms}ms  must={must_sym}  should={r.should_score:.2f}  n={r.result_count}")

    print_report(results)

    if args.json_out:
        out = [
            {
                "id": r.case_id,
                "category": r.category,
                "ok": r.ok,
                "must_pass": r.must_pass,
                "should_score": r.should_score,
                "latency_ms": r.latency_ms,
                "error": r.error,
                "skipped": r.skipped,
            }
            for r in results
        ]
        with open(args.json_out, "w") as f:
            json.dump(out, f, indent=2)
        print(f"Raw results → {args.json_out}")


if __name__ == "__main__":
    main()
