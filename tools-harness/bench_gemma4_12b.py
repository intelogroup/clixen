"""
Benchmark gemma4:12b-mlx vs gemma4:e4b vs gemma4:e2b on the summarizer cases.

Runs the same pre-fetched snippets through each model and scores output.
Also tests raw Q&A speed/quality on a 5-prompt smoke set.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import tools.search_agentic as _sa
from tools.search_agentic import summarize_with_model
from tools.search_result import SearchSnippet

MODELS = ["gemma4:e2b", "gemma4:e4b", "gemma4:12b-mlx"]

CASES = [
    {
        "id": "ucl_quarters",
        "query": "What are the UEFA Champions League quarter-final results in April 2026?",
        "snippets": [
            SearchSnippet(title="Real Madrid 3 - 1 Arsenal | UCL QF First Leg",
                snippet="Real Madrid beat Arsenal 3-1 in the first leg of the UCL quarter-final at the Bernabeu. Vinicius Jr scored twice and Bellingham added a third.",
                url="https://www.uefa.com/uefachampionsleague/match/real-madrid-arsenal/"),
            SearchSnippet(title="Barcelona 4 - 2 Borussia Dortmund | UCL QF",
                snippet="Barcelona dismantled Borussia Dortmund 4-2 at Camp Nou. Lewandowski scored a hat-trick. Dortmund replied through Guirassy.",
                url="https://www.uefa.com/uefachampionsleague/match/barcelona-dortmund/"),
            SearchSnippet(title="Bayern Munich 2 - 0 Inter Milan | UCL Quarter Final",
                snippet="Bayern Munich kept a clean sheet against Inter Milan, winning 2-0 at the Allianz Arena. Kane and Musiala scored in the second half.",
                url="https://www.bbc.co.uk/sport/football/champions-league"),
        ],
        "must_have": ["real madrid", "arsenal", "barcelona", "dortmund", "bayern"],
        "nice_have": ["vinicius", "lewandowski", "kane", "3-1", "4-2", "2-0"],
        "hallucination_markers": ["i think", "as an ai", "may have", "i cannot confirm"],
    },
    {
        "id": "la_liga_top_scorer",
        "query": "Who is the top scorer in La Liga in the 2025-26 season?",
        "snippets": [
            SearchSnippet(title="La Liga top scorers 2025-26 season | Transfermarkt",
                snippet="Robert Lewandowski leads La Liga with 28 goals in the 2025-26 season, ahead of Vinicius Jr (22) and Morata (19). Barcelona striker is on course for the Pichichi trophy.",
                url="https://www.transfermarkt.com/laliga/torschuetzenliste/"),
            SearchSnippet(title="Lewandowski closing in on Pichichi — LaLiga",
                snippet="Lewandowski has scored in 7 consecutive La Liga matches and needs just 3 more goals to break the 30-goal barrier this season.",
                url="https://www.laliga.com/en-ES/news/lewandowski-pichichi"),
            SearchSnippet(title="Barcelona vs Espanyol — match report",
                snippet="Barcelona won 4-1 with Lamine Yamal and Lewandowski each scoring twice.",
                url="https://www.goal.com/barcelona-espanyol"),
        ],
        "must_have": ["lewandowski", "28"],
        "nice_have": ["pichichi", "barcelona", "vinicius", "goals"],
        "hallucination_markers": ["i think", "as an ai", "unclear", "not sure"],
    },
    {
        "id": "multistep_arithmetic",
        "query": "If a stock starts at $50, drops 20%, then rises 30%, what is the final price?",
        "snippets": [],
        "must_have": ["44", "20", "30", "50"],
        "nice_have": ["percent", "drop", "rise", "calculation"],
        "hallucination_markers": ["i think", "as an ai"],
    },
    {
        "id": "code_explanation",
        "query": "Explain what this Python function does: `def f(x): return x if x <= 1 else f(x-1) + f(x-2)`",
        "snippets": [],
        "must_have": ["fibonacci", "recursive", "recursion"],
        "nice_have": ["function", "calls itself", "1"],
        "hallucination_markers": ["i think", "as an ai", "i believe"],
    },
]


def score(response: str, case: dict) -> dict:
    resp_lower = response.lower()
    must = sum(2 for kw in case["must_have"] if kw.lower() in resp_lower)
    nice = sum(1 for kw in case["nice_have"] if kw.lower() in resp_lower)
    hall = -3 * sum(1 for m in case["hallucination_markers"] if m in resp_lower)
    terse = -2 if len(response.split()) <= 15 else 0
    bloated = -1 if len(response.split()) > 300 else 0
    total = must + nice + hall + terse + bloated
    return {
        "must_hits": must // 2, "nice_hits": nice,
        "hall_pen": hall, "length_pen": terse + bloated,
        "total": total,
        "word_count": len(response.split()),
    }


def warmup(model: str):
    """Pre-allocate KV cache to avoid first-request tax."""
    print(f"  Warmup {model}...", end=" ", flush=True)
    t0 = time.time()
    _sa._SUMMARIZER_MODEL = model
    # Make a noop call
    try:
        from clients.ollama_client import chat as _ollama_chat
        _ollama_chat("hi", model=model, options={"num_predict": 5})
    except Exception as e:
        print(f"warn: {e}")
    print(f"{time.time()-t0:.1f}s")


def run_model(model: str, cases: list, warm: bool = True) -> dict:
    print(f"\n=== {model} ===")
    if warm:
        warmup(model)
    _sa._SUMMARIZER_MODEL = model
    results = []
    for case in cases:
        t0 = time.time()
        try:
            response = summarize_with_model(case["query"], case["snippets"])
        except Exception as e:
            response = f"ERROR: {e}"
        elapsed = time.time() - t0
        s = score(response, case)
        s["response"] = response[:200]
        s["elapsed"] = round(elapsed, 2)
        print(f"  {case['id']:30s} score={s['total']:>3d}  {elapsed:.1f}s  words={s['word_count']}")
        results.append(s)
    return {"model": model, "cases": results,
            "total_score": sum(r["total"] for r in results),
            "avg_elapsed": sum(r["elapsed"] for r in results) / len(results)}


if __name__ == "__main__":
    all_results = []
    for m in MODELS:
        r = run_model(m, CASES, warm=True)
        all_results.append(r)
        time.sleep(1)  # cool down between models

    print("\n" + "=" * 60)
    print("RANKING")
    print("=" * 60)
    for r in sorted(all_results, key=lambda x: x["total_score"], reverse=True):
        print(f"  {r['model']:15s}  score={r['total_score']:>4d}  avg_time={r['avg_elapsed']:.2f}s")

    out_path = Path("/tmp/gemma4_12b_benchmark.json")
    out_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nFull results: {out_path}")
