"""
benchmark_summarizer_models.py — find the best model for search summarization.

Runs every CANDIDATE model through the same summarize_with_model() call on a
fixed set of pre-fetched snippets. Sports queries use deterministic_summary (no
model) and are excluded; this test targets the LLM synthesis path only.

Usage:
  python benchmark_summarizer_models.py
  python benchmark_summarizer_models.py --live   # also hits real APIs to fetch snippets

Scoring (per response):
  +2  each must_have keyword present
  +1  each nice_have keyword present
  -3  each hallucination marker present
  -2  answer is ≤ 15 words (too terse / no synthesis)
  -1  answer > 300 words (bloated)
"""
from __future__ import annotations

import sys, time, textwrap, json
from pathlib import Path

sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import tools.search_agentic as _sa
from tools.search_agentic import summarize_with_model
from tools.search_result import SearchSnippet

# ---------------------------------------------------------------------------
# Candidate models — ordered from lightest to heaviest
# ---------------------------------------------------------------------------
CANDIDATES = [
    "qwen3.5:2b",
    "qwen3.5:4b",
    "qwen3.5:9b",
    "gemma4",
]

# ---------------------------------------------------------------------------
# Fixed test cases — snippets are hand-crafted to match what the pipeline
# realistically returns for each query type.
# ---------------------------------------------------------------------------
CASES = [
    {
        "id": "ucl_quarters_multi_match",
        "query": "What are the UEFA Champions League quarter-final results in April 2026?",
        "desc": "Multi-source synthesis — 3 different matches, must combine all",
        "snippets": [
            SearchSnippet(
                title="Real Madrid 3 - 1 Arsenal | UCL QF First Leg",
                snippet="Real Madrid beat Arsenal 3-1 in the first leg of the UCL quarter-final at the Bernabeu. Vinicius Jr scored twice and Bellingham added a third.",
                url="https://www.uefa.com/uefachampionsleague/match/real-madrid-arsenal/",
            ),
            SearchSnippet(
                title="Barcelona 4 - 2 Borussia Dortmund | UCL QF",
                snippet="Barcelona dismantled Borussia Dortmund 4-2 at Camp Nou. Lewandowski scored a hat-trick. Dortmund replied through Guirassy.",
                url="https://www.uefa.com/uefachampionsleague/match/barcelona-dortmund/",
            ),
            SearchSnippet(
                title="Bayern Munich 2 - 0 Inter Milan | UCL Quarter Final",
                snippet="Bayern Munich kept a clean sheet against Inter Milan, winning 2-0 at the Allianz Arena. Kane and Musiala scored in the second half.",
                url="https://www.bbc.co.uk/sport/football/champions-league",
            ),
        ],
        "must_have": ["real madrid", "arsenal", "barcelona", "dortmund", "bayern"],
        "nice_have": ["vinicius", "lewandowski", "kane", "3-1", "4-2", "2-0"],
        "hallucination_markers": ["i think", "as an ai", "may have", "i cannot confirm"],
    },
    {
        "id": "la_liga_top_scorer",
        "query": "Who is the top scorer in La Liga in the 2025-26 season?",
        "desc": "Single-fact extraction from mixed snippets",
        "snippets": [
            SearchSnippet(
                title="La Liga top scorers 2025-26 season | Transfermarkt",
                snippet="Robert Lewandowski leads La Liga with 28 goals in the 2025-26 season, ahead of Vinicius Jr (22) and Morata (19). Barcelona striker is on course for the Pichichi trophy.",
                url="https://www.transfermarkt.com/laliga/torschuetzenliste/",
            ),
            SearchSnippet(
                title="Lewandowski closing in on Pichichi — LaLiga",
                snippet="Lewandowski has scored in 7 consecutive La Liga matches and needs just 3 more goals to break the 30-goal barrier this season.",
                url="https://www.laliga.com/en-ES/news/lewandowski-pichichi",
            ),
            SearchSnippet(
                title="Barcelona vs Espanyol — match report",
                snippet="Barcelona won 4-1 with Lamine Yamal and Lewandowski each scoring twice.",
                url="https://www.goal.com/barcelona-espanyol",
            ),
        ],
        "must_have": ["lewandowski", "28"],
        "nice_have": ["pichichi", "barcelona", "vinicius", "goals"],
        "hallucination_markers": ["i think", "as an ai", "unclear", "not sure"],
    },
    {
        "id": "epl_table_top4",
        "query": "What is the current Premier League top-4 table in April 2026?",
        "desc": "Table extraction — must list multiple teams with points",
        "snippets": [
            SearchSnippet(
                title="Premier League table 2025-26 — BBC Sport",
                snippet="1. Arsenal 76 pts  2. Liverpool 74 pts  3. Manchester City 68 pts  4. Chelsea 63 pts. Arsenal lead by 2 points with 6 games remaining.",
                url="https://www.bbc.co.uk/sport/football/premier-league/table",
            ),
            SearchSnippet(
                title="Arsenal vs Liverpool title race — The Guardian",
                snippet="Arsenal's 2-0 win over Tottenham moves them 2 points clear of Liverpool at the top of the Premier League table.",
                url="https://www.theguardian.com/football/premier-league",
            ),
            SearchSnippet(
                title="Man City top 4 hopes — Sky Sports",
                snippet="Manchester City are 8 points behind league leaders Arsenal but remain in third place, 5 points ahead of Chelsea in fourth.",
                url="https://www.skysports.com/premier-league",
            ),
        ],
        "must_have": ["arsenal", "liverpool", "manchester city", "chelsea"],
        "nice_have": ["76", "74", "68", "63", "points"],
        "hallucination_markers": ["i think", "as an ai", "maybe", "could be"],
    },
    {
        "id": "transfer_news_synthesis",
        "query": "What are the latest transfer news for Real Madrid this summer?",
        "desc": "News synthesis — multiple rumours from different sources",
        "snippets": [
            SearchSnippet(
                title="Real Madrid targeting Trent Alexander-Arnold — Fabrizio Romano",
                snippet="Real Madrid have made Trent Alexander-Arnold their top priority for the summer transfer window. The Liverpool defender's contract expires in June 2026.",
                url="https://www.fabrizio-romano.com/real-madrid-trent",
            ),
            SearchSnippet(
                title="Endrick expected to start next season — Marca",
                snippet="Endrick is set to take on a bigger role at Real Madrid next season after impressing Carlo Ancelotti in training. The 18-year-old Brazilian is not for sale.",
                url="https://www.marca.com/endrick-real-madrid",
            ),
            SearchSnippet(
                title="Real Madrid consider Bellingham replacement",
                snippet="Real Madrid are monitoring Florian Wirtz as a potential long-term addition to their midfield. Bayer Leverkusen value the German at €150m.",
                url="https://www.goal.com/real-madrid-wirtz",
            ),
        ],
        "must_have": ["real madrid", "trent", "endrick"],
        "nice_have": ["alexander-arnold", "wirtz", "summer", "transfer"],
        "hallucination_markers": ["i think", "as an ai", "i cannot", "unclear"],
    },
    {
        "id": "weak_contradictory_sources",
        "query": "Who won the Copa del Rey final 2026?",
        "desc": "Conflicting snippets — model must pick the most authoritative signal",
        "snippets": [
            SearchSnippet(
                title="Copa del Rey Final 2026 result",
                snippet="Athletic Club beat Real Betis 2-1 in the Copa del Rey final at Estadio La Cartuja. Williams brothers both scored for Athletic.",
                url="https://www.rfef.es/copa-del-rey/final",
            ),
            SearchSnippet(
                title="Copa del Rey latest updates",
                snippet="Copa del Rey final takes place in Seville. Both Athletic and Betis fans heading to Estadio La Cartuja.",
                url="https://www.marca.com/copa-del-rey",
            ),
            SearchSnippet(
                title="Copa del Rey: preview and prediction",
                snippet="Pundits are split on the Copa del Rey final. Betis are slight favourites according to bookmakers but Athletic's recent form is strong.",
                url="https://www.skysports.com/copa-del-rey-preview",
            ),
        ],
        "must_have": ["athletic", "betis", "2-1"],
        "nice_have": ["williams", "copa del rey", "seville"],
        "hallucination_markers": ["i think", "as an ai", "unclear who", "not confirmed"],
    },
]

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score(response: str, case: dict) -> tuple[int, list[str]]:
    low = response.lower()
    notes = []
    total = 0

    for kw in case["must_have"]:
        if kw.lower() in low:
            total += 2
        else:
            notes.append(f"MISSING must_have: {kw!r}")

    for kw in case["nice_have"]:
        if kw.lower() in low:
            total += 1

    for marker in case["hallucination_markers"]:
        if marker.lower() in low:
            total -= 3
            notes.append(f"HALLUCINATION MARKER: {marker!r}")

    word_count = len(response.split())
    if word_count <= 15:
        total -= 2
        notes.append(f"TOO TERSE ({word_count} words)")
    elif word_count > 300:
        total -= 1
        notes.append(f"BLOATED ({word_count} words)")

    return total, notes


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
SEP = "=" * 72
SEP2 = "-" * 72

def run():
    results: dict[str, dict] = {m: {"total": 0, "cases": []} for m in CANDIDATES}

    for case in CASES:
        print(f"\n{SEP}")
        print(f"CASE: {case['id']}  —  {case['desc']}")
        print(f"Query: {case['query']}")
        print(SEP2)

        case_scores = {}
        case_answers = {}

        for model in CANDIDATES:
            _sa._SUMMARIZER_MODEL = model
            t0 = time.time()
            try:
                answer = summarize_with_model(case["query"], case["snippets"])
            except Exception as e:
                answer = f"[ERROR: {e}]"
            elapsed = time.time() - t0

            sc, notes = score(answer, case)
            case_scores[model] = sc
            case_answers[model] = answer

            results[model]["total"] += sc
            results[model]["cases"].append({
                "id": case["id"],
                "score": sc,
                "elapsed": round(elapsed, 1),
                "answer": answer,
                "notes": notes,
            })

            flag = "+" if sc > 0 else ("=" if sc == 0 else "!")
            print(f"\n  [{flag}] {model:20s}  score={sc:+d}  {elapsed:.1f}s")
            print(textwrap.indent(textwrap.fill(answer, 90), "      "))
            if notes:
                for n in notes:
                    print(f"        >> {n}")

    # ---------------------------------------------------------------------------
    # Final leaderboard
    # ---------------------------------------------------------------------------
    print(f"\n{SEP}")
    print("LEADERBOARD")
    print(SEP2)
    ranked = sorted(CANDIDATES, key=lambda m: results[m]["total"], reverse=True)
    max_score = len(CASES) * (2 * max(len(c["must_have"]) for c in CASES) + len(CASES))
    for rank, model in enumerate(ranked, 1):
        total = results[model]["total"]
        print(f"  #{rank}  {model:20s}  total={total:+d}")

    winner = ranked[0]
    print(f"\n  Best model for summarization: {winner}")
    print(SEP)

    # Save full results
    out_path = Path(__file__).parent / "benchmark_summarizer_results.json"
    with open(out_path, "w") as f:
        json.dump({"candidates": CANDIDATES, "cases": [c["id"] for c in CASES], "results": results}, f, indent=2)
    print(f"\n  Full results saved to: {out_path}")


if __name__ == "__main__":
    run()
