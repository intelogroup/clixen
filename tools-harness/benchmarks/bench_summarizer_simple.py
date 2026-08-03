#!/usr/bin/env python3
"""
Benchmark: gemma4 vs mistral-nemo with SIMPLE prompt.
Test if simpler prompt = better quality + still fast.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools.search_agentic import _evidence_block, _extract_answer
from tools.search_result import SearchSnippet
import ollama


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


SIMPLE_PROMPT = """Answer the question using ONLY the evidence below.

 Evidence:
{EVIDENCE}

Question: {QUERY}

Answer:"""


def run_summarize_simple(query: str, model: str, items: list) -> tuple[str, float]:
    """Run with simple stripped-down prompt."""
    prompt = SIMPLE_PROMPT.format(
        EVIDENCE=_evidence_block(items),
        QUERY=query,
    )

    t0 = time.time()
    resp = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": "You answer questions directly. Use facts from evidence. Keep answers short. Start with the answer."},
            {"role": "user", "content": prompt},
        ],
        options={"temperature": 0.3, "top_p": 0.9},
        think=False,
        stream=False,
    )
    elapsed = time.time() - t0

    answer = resp.message.content or ""
    # Clean up any leading "Answer:" the model adds
    if answer.lower().startswith("answer:"):
        answer = answer[7:].strip()
    return answer, elapsed


def load_test_cases(path: str = "hard_search_cases.json", limit: int = 20) -> list[dict]:
    with open(path) as f:
        cases = json.load(f)
    return cases[:limit]


async def main():
    cases_path = Path(__file__).parent / "hard_search_cases.json"
    cases = load_test_cases(str(cases_path), limit=5)  # Only 5 cases for quick test

    models = ["gemma4", "mistral-nemo"]
    results = {m: [] for m in models}

    print(f"Loaded {len(cases)} test cases")
    print(f"Testing with SIMPLE prompt: {models}\n")

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
                case_key = q[:30].lower().replace(" ", "_")
                items = MOCK_EVIDENCE.get(case_key, [
                    SearchSnippet(title="Test", url="example.com", published_date="2026-04-24", snippet=f"Evidence for: {q}", source="mock"),
                ])

                answer, elapsed = run_summarize_simple(q, model, items)

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
    print("SUMMARY - SIMPLE PROMPT")
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

    # Show quality comparison
    print(f"\n{'='*60}")
    print("QUALITY CHECK")
    print(f"{'='*60}")
    for i, case in enumerate(cases[:5], 1):
        q = case.get("question", "")
        case_id = case.get("id", f"case_{i}")
        print(f"\n[{i}] {case_id}: {q[:50]}...")
        for model in models:
            r = results[model][i-1] if i-1 < len(results[model]) else {}
            ans = r.get("answer", "(error)")[:60]
            print(f"    {model}: {ans}")

    # Save
    output = Path(__file__).parent / "summarizer_simple_bench_results.json"
    with open(output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output}")


if __name__ == "__main__":
    asyncio.run(main())