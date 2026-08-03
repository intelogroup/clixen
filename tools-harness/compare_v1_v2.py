#!/usr/bin/env python3
"""
Side-by-side comparison: Search Graph V1 (5 backends) vs V2 (SearXNG).
Runs 8 curated questions through both graphs and scores results.
"""
import sys
import os
import json
import time
import textwrap
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from agents.search_graph import run_search_graph
from agents.search_graph_v2 import run_search_graph_v2

QUERIES = [
    {
        "q": "Who won the 2024 UEFA Champions League final?",
        "intent": "historical", "domain": "sports",
        "keywords": ["real madrid"],
        "answer": "Real Madrid beat Borussia Dortmund 2-0",
    },
    {
        "q": "What is the capital of Australia?",
        "intent": "factual", "domain": "general",
        "keywords": ["canberra"],
        "answer": "Canberra",
    },
    {
        "q": "Who is the CEO of OpenAI as of 2025?",
        "intent": "recent", "domain": "general",
        "keywords": ["sam altman", "altman"],
        "answer": "Sam Altman",
    },
    {
        "q": "What programming language is the Linux kernel written in?",
        "intent": "tech", "domain": "code",
        "keywords": ["c", "c language"],
        "answer": "C",
    },
    {
        "q": "What year did SpaceX first successfully land an orbital rocket booster?",
        "intent": "historical", "domain": "general",
        "keywords": ["2015"],
        "answer": "2015",
    },
    {
        "q": "What is Bitcoin trading at in USD right now?",
        "intent": "live", "domain": "finance",
        "keywords": ["bitcoin", "usd", "$"],
        "answer": "current Bitcoin price in USD",
    },
    {
        "q": "What is the latest stable Python release version?",
        "intent": "tech", "domain": "code",
        "keywords": ["python", "3."],
        "answer": "Python 3.x latest stable",
    },
    {
        "q": "When did Apple release the first iPhone?",
        "intent": "historical", "domain": "general",
        "keywords": ["2007"],
        "answer": "2007",
    },
]

SEP = "=" * 90

def verdict(response: str, keywords: list[str]) -> bool:
    low = response.lower()
    return any(k.lower() in low for k in keywords)


def run_one(graph_fn, item, label):
    """Run a single query through a graph and return result dict."""
    t0 = time.time()
    error = None
    try:
        resp = graph_fn(item["q"], item["intent"], item["domain"])
    except Exception as e:
        resp = f"ERROR: {e}"
        error = str(e)
    elapsed = time.time() - t0
    passed = verdict(resp, item["keywords"])
    # Measure response length as a proxy for informativeness
    resp_len = len(resp) if resp else 0
    return {
        "label": label,
        "passed": passed,
        "latency_s": round(elapsed, 2),
        "resp_len": resp_len,
        "response": resp,
        "error": error,
    }


def main():
    print(SEP)
    print("SEARCH GRAPH COMPARISON: V1 (5 backends) vs V2 (SearXNG)")
    print(SEP)
    print()

    results = {"v1_results": [], "v2_results": [], "head_to_head": []}

    for i, item in enumerate(QUERIES, 1):
        print(f"[{i}/{len(QUERIES)}] Query: {item['q']}")
        print(f"     Intent: {item['intent']:12s}  Domain: {item['domain']}")
        print(f"     Expected answer: {item['answer']}")
        print()

        # Run V1
        print("  V1 (5 backends: Discovery + Brave + Exa + SerpAPI + DDG):")
        v1 = run_one(run_search_graph, item, "V1")
        status1 = "PASS" if v1["passed"] else "FAIL"
        print(f"    [{status1}]  {v1['latency_s']:.1f}s  {v1['resp_len']} chars")
        print(textwrap.indent(v1["response"][:300], "    "))
        if len(v1["response"]) > 300:
            print(f"    ... (+{len(v1['response']) - 300} more chars)")
        print()

        # Run V2
        print("  V2 (SearXNG: Discovery + SearXNG with Exa+Brave fallback):")
        v2 = run_one(run_search_graph_v2, item, "V2")
        status2 = "PASS" if v2["passed"] else "FAIL"
        print(f"    [{status2}]  {v2['latency_s']:.1f}s  {v2['resp_len']} chars")
        print(textwrap.indent(v2["response"][:300], "    "))
        if len(v2["response"]) > 300:
            print(f"    ... (+{len(v2['response']) - 300} more chars)")
        print()

        results["v1_results"].append({
            "query": item["q"], "passed": v1["passed"],
            "latency_s": v1["latency_s"], "resp_len": v1["resp_len"],
        })
        results["v2_results"].append({
            "query": item["q"], "passed": v2["passed"],
            "latency_s": v2["latency_s"], "resp_len": v2["resp_len"],
        })

        # Head to head verdict
        if v1["passed"] and not v2["passed"]:
            winner = "V1"
        elif v2["passed"] and not v1["passed"]:
            winner = "V2"
        elif v1["passed"] and v2["passed"]:
            winner = "BOTH"
        else:
            winner = "NEITHER"
        results["head_to_head"].append({
            "query": item["q"], "winner": winner,
            "v1_passed": v1["passed"], "v2_passed": v2["passed"],
        })
        print(f"  Head-to-head: {winner}")
        print(f"  {SEP[:80]}")
        print()

    # ── Summary ──
    print(SEP)
    print("SUMMARY")
    print(SEP)
    print()

    v1_passed = sum(1 for r in results["v1_results"] if r["passed"])
    v2_passed = sum(1 for r in results["v2_results"] if r["passed"])
    v1_lat = sum(r["latency_s"] for r in results["v1_results"]) / len(results["v1_results"])
    v2_lat = sum(r["latency_s"] for r in results["v2_results"]) / len(results["v2_results"])
    v1_len = sum(r["resp_len"] for r in results["v1_results"])
    v2_len = sum(r["resp_len"] for r in results["v2_results"])

    print(f"  V1 (5 backends):     {v1_passed}/{len(QUERIES)} passed | avg latency: {v1_lat:.1f}s | total chars: {v1_len}")
    print(f"  V2 (SearXNG):        {v2_passed}/{len(QUERIES)} passed | avg latency: {v2_lat:.1f}s | total chars: {v2_len}")
    print()
    print(f"  V1 wins:  {sum(1 for h in results['head_to_head'] if h['winner'] == 'V1')}")
    print(f"  V2 wins:  {sum(1 for h in results['head_to_head'] if h['winner'] == 'V2')}")
    print(f"  Both:     {sum(1 for h in results['head_to_head'] if h['winner'] == 'BOTH')}")
    print(f"  Neither:  {sum(1 for h in results['head_to_head'] if h['winner'] == 'NEITHER')}")
    print()

    # Save detailed results
    out = Path(__file__).parent / "compare_v1_v2_results.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Detailed results saved to: {out}")
    print()


if __name__ == "__main__":
    main()
