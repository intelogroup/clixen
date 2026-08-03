#!/usr/bin/env python3
"""
Benchmark: gemma4 vs mistral-nemo as search summarizer.
Uses hard_search_cases.json for test queries.
Tests the REAL summarize_with_model function (not a mock prompt).
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools.search_agentic import _evidence_block, _extract_answer, summarize_with_model
from tools.search_result import SearchSnippet


MOCK_EVIDENCE = {
    "live_finance_btc_usd": [
        SearchSnippet(title="Bitcoin USD", url="coinbase.com", published_date="2026-04-24", snippet="BTC/USD $94,250 (+2.3%) in the last 24 hours. Trading near resistance at $95K.", source="mock"),
        SearchSnippet(title="BTC Price", url="binance.com", published_date="2026-04-24", snippet="Bitcoin is up 2.1% today, currently $94,200. Volume at $28B.", source="mock"),
    ],
    "live_finance_nvda_move": [
        SearchSnippet(title="NVDA Stock", url="reuters.com", published_date="2026-04-24", snippet="NVDA +3.2% on strong Q1 guidance. Data center revenue beat estimates.", source="mock"),
        SearchSnippet(title="Nvidia Earnings", url="bloomberg.com", published_date="2026-04-24", snippet="Nvidia shares rose after CFO mentioned strong AI chip demand.", source="mock"),
    ],
    "tech_latest_python_release": [
        SearchSnippet(title="Python 3.14", url="python.org", published_date="2026-03-15", snippet="Python 3.14.0 released March 2026. Faster startup, new typing features.", source="mock"),
        SearchSnippet(title="Python Release", url="pypi.org", published_date="2026-03-16", snippet="Latest stable is 3.14.0 with 15% faster startup time.", source="mock"),
    ],
    "company_person_latest_ceo": [
        SearchSnippet(title="Microsoft CEO", url="microsoft.com", published_date="2026-01-01", snippet="Satya Nadella is CEO of Microsoft since 2014.", source="mock"),
        SearchSnippet(title="Satya Nadella", url="wikipedia.org", published_date="2026-01-01", snippet="Satya Nadella has been CEO since February 2014.", source="mock"),
    ],
    "historical_stable_fact": [
        SearchSnippet(title="US President", url="history.com", published_date="2020-01-01", snippet="George Washington was the first US President, serving 1789-1797.", source="mock"),
        SearchSnippet(title="First President", url="archives.gov", published_date="2020-01-01", snippet="George Washington unanimously elected by Electoral College.", source="mock"),
    ],
}


def load_test_cases(path: str = "hard_search_cases.json", limit: int = 20) -> list[dict]:
    """Load test cases from the JSON file."""
    with open(path) as f:
        cases = json.load(f)
    return cases[:limit]


async def run_summarize(query: str, model: str, intent: str = "search") -> tuple[str, float]:
    """Run summarize_with_model with the specified model (the REAL function)."""
    case_id = query[:30].lower().replace(" ", "_")
    items = MOCK_EVIDENCE.get(case_id, [
        SearchSnippet(title="Test", url="example.com", published_date="2026-04-24", snippet=f"Evidence for: {query}", source="mock"),
    ])

    t0 = time.time()
    answer = summarize_with_model(
        query=query,
        items=items,
        intent=intent,
        domain="general",
        model=model,
        timeout_s=30.0,
    )
    elapsed = time.time() - t0

    return answer, elapsed


async def main():
    cases_path = Path(__file__).parent / "hard_search_cases.json"
    cases = load_test_cases(str(cases_path), limit=15)

    models = ["gemma4", "mistral-nemo"]
    results = {m: [] for m in models}

    print(f"Loaded {len(cases)} test cases")
    print(f"Testing summarizers: {models}\n")

    for i, case in enumerate(cases, 1):
        q = case.get("question", "")
        intent = case.get("intent", "search")
        case_id = case.get("id", f"case_{i}")

        if not q:
            continue

        print(f"[{i}/{len(cases)}] {case_id}")
        print(f"  Query: {q[:60]}...")

        for model in models:
            try:
                answer, elapsed = await run_summarize(q, model, intent)

                results[model].append({
                    "case_id": case_id,
                    "query": q,
                    "answer": answer[:200] if answer else "(empty)",
                    "latency_sec": round(elapsed, 2),
                })

                preview = answer.replace("\n", " ")[:50] if answer else "(empty)"
                print(f"    {model}: {elapsed:.1f}s -> {preview}")
            except Exception as e:
                print(f"    {model}: ERROR {e}")
                results[model].append({
                    "case_id": case_id,
                    "error": str(e),
                    "latency_sec": 0,
                })

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    for model in models:
        latencies = [r["latency_sec"] for r in results[model] if r.get("latency_sec", 0) > 0]
        if latencies:
            avg = sum(latencies) / len(latencies)
            total = sum(latencies)
            print(f"{model}: avg={avg:.1f}s, total={total:.1f}s ({len(latencies)} cases)")

    # Compare
    g4_lat = [r["latency_sec"] for r in results["gemma4"] if r.get("latency_sec", 0) > 0]
    nemo_lat = [r["latency_sec"] for r in results["mistral-nemo"] if r.get("latency_sec", 0) > 0]

    if g4_lat and nemo_lat:
        g4_avg = sum(g4_lat) / len(g4_lat)
        nemo_avg = sum(nemo_lat) / len(nemo_lat)

        print(f"\n{'─'*60}")
        if g4_avg < nemo_avg:
            speedup = (nemo_avg / g4_avg - 1) * 100
            print(f"WINNER: gemma4 {speedup:.0f}% faster ({g4_avg:.1f}s vs {nemo_avg:.1f}s)")
        else:
            speedup = (g4_avg / nemo_avg - 1) * 100
            print(f"WINNER: mistral-nemo {speedup:.0f}% faster ({nemo_avg:.1f}s vs {g4_avg:.1f}s)")

    # Save
    output = Path(__file__).parent / "summarizer_bench_results.json"
    with open(output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output}")


if __name__ == "__main__":
    asyncio.run(main())