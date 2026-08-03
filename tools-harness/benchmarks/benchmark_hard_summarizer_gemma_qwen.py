"""
Hard summarizer benchmark: gemma4 vs qwen3.5:4b.

This targets the LLM synthesis path with prebuilt snippets, avoiding live search
variance while stressing grounding, conflict handling, arithmetic, missing-data
refusal, noise filtering, and requested output shape.
"""
from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from tools.search_agentic import summarize_with_model
from tools.search_result import SearchSnippet


CANDIDATES = ["gemma4", "qwen3.5:4b"]


def s(title: str, snippet: str, url: str = "https://example.com", date: str = "") -> SearchSnippet:
    return SearchSnippet(title=title, snippet=snippet, url=url, published_date=date, source="mock")


CASES = [
    {
        "id": "conflict_authoritative_release",
        "query": "What is the latest stable version of the AtlasDB Python client?",
        "desc": "Conflicting source authority: official docs beat blogs and stale package pages",
        "snippets": [
            s("AtlasDB client changelog", "AtlasDB Python client 2.8.1 was released on 2026-04-21 and is marked stable. Version 2.9.0 is beta only.", "https://docs.atlasdb.test/changelog", "2026-04-21"),
            s("AtlasDB 2.9 preview", "A blog post says AtlasDB Python client 2.9.0 beta adds async migrations.", "https://blog.example.test/atlasdb-29", "2026-04-22"),
            s("PyPI AtlasDB", "Latest stable release listed here is 2.7.4, uploaded 2026-02-10.", "https://pypi.org/project/atlasdb", "2026-02-10"),
            s("Forum thread", "Someone says they installed 3.0 nightly, but it is not a stable release.", "https://forum.example.test/atlasdb", "2026-04-23"),
        ],
        "intent": "recent",
        "domain": "code",
        "must_have": ["2.8.1", "stable"],
        "nice_have": ["2026-04-21", "2.9.0", "beta"],
        "forbidden": ["2.9.0 is stable", "3.0", "2.7.4 is the latest"],
    },
    {
        "id": "math_excluding_penalties",
        "query": "How many league goals did Mina Costa score in 2025-26 excluding penalties?",
        "desc": "Arithmetic under exclusion: subtract penalties from total",
        "snippets": [
            s("Mina Costa stats", "Mina Costa scored 31 league goals in 2025-26. Of those, 7 were penalties and 24 were from open play or set pieces.", "https://stats.example.test/mina"),
            s("Golden boot race", "Mina Costa leads the league with 31 goals, ahead of Ana Ruiz on 28.", "https://sports.example.test/golden-boot"),
            s("Penalty takers", "Costa converted 7 penalties in league play during the 2025-26 season.", "https://sports.example.test/penalties"),
        ],
        "intent": "search",
        "domain": "sports",
        "must_have": ["31", "7", "24"],
        "nice_have": ["excluding penalties", "31 total", "7 penalties"],
        "forbidden": ["31 goals excluding", "28", "cannot"],
    },
    {
        "id": "missing_specific_value_refusal",
        "query": "What was NovaGrid's Q4 2025 free cash flow?",
        "desc": "Must refuse exact value when snippets include revenue and EBITDA but not FCF",
        "snippets": [
            s("NovaGrid Q4 earnings", "NovaGrid reported Q4 2025 revenue of $418 million and adjusted EBITDA of $72 million.", "https://ir.novagrid.test/q4"),
            s("NovaGrid balance sheet", "Cash and equivalents were $190 million at year end. Debt was $510 million.", "https://finance.example.test/novagrid"),
            s("Analyst note", "The company did not disclose free cash flow in the Q4 2025 press release.", "https://analyst.example.test/novagrid"),
        ],
        "intent": "recent",
        "domain": "finance",
        "must_have": ["not found"],
        "nice_have": ["free cash flow", "did not disclose"],
        "forbidden": ["$418", "$72", "$190", "$510"],
    },
    {
        "id": "noisy_seo_filter",
        "query": "What are the actual symptoms of adult RSV listed by Medline Health?",
        "desc": "Ignore SEO/product noise and answer from named medical source",
        "snippets": [
            s("Buy RSV supplements", "Our immune gummies help with winter wellness. Limited time discount.", "https://shop.example.test/rsv"),
            s("Medline Health RSV in adults", "Adult RSV symptoms include runny nose, decrease in appetite, coughing, sneezing, fever, and wheezing. Severe cases can cause trouble breathing.", "https://medline.example.test/rsv-adults"),
            s("Top 10 RSV hacks", "Influencers recommend steam, turmeric, and sleep tracking for cold season.", "https://seo.example.test/rsv-hacks"),
            s("Medline Health update", "Seek urgent care for difficulty breathing, blue lips, or dehydration symptoms.", "https://medline.example.test/rsv-warning"),
        ],
        "intent": "search",
        "domain": "health",
        "must_have": ["runny nose", "coughing", "sneezing", "fever", "wheezing"],
        "nice_have": ["trouble breathing", "appetite"],
        "forbidden": ["gummies", "turmeric", "sleep tracking"],
    },
    {
        "id": "stale_vs_current_price",
        "query": "What is the current price of the HelioPad X2 and did it change from launch?",
        "desc": "Current evidence vs stale launch article",
        "snippets": [
            s("HelioPad X2 launch", "At launch in January 2026, the HelioPad X2 started at $699.", "https://news.example.test/heliopad-launch", "2026-01-08"),
            s("HelioPad X2 store page", "Current price: HelioPad X2 256GB starts at $649 after a permanent $50 price cut.", "https://store.heliopad.test/x2", "2026-04-25"),
            s("Retail roundup", "Retailers list HelioPad X2 at $649 for the base 256GB model this week.", "https://retail.example.test/heliopad", "2026-04-24"),
        ],
        "intent": "live",
        "domain": "pricing",
        "must_have": ["$649", "$699", "$50"],
        "nice_have": ["price cut", "current", "launch"],
        "forbidden": ["$699 current", "$699 now", "unchanged", "annual cost", "winner"],
    },
    {
        "id": "multi_hop_acquisition",
        "query": "Who owns VectorMail now, and which acquisition caused that?",
        "desc": "Two-step chain: parent acquired company that previously acquired target",
        "snippets": [
            s("VectorMail acquired by BrightSuite", "BrightSuite acquired VectorMail in 2023 and kept the VectorMail brand.", "https://dealbook.example.test/vectormail"),
            s("CloudNorth closes BrightSuite deal", "CloudNorth completed its acquisition of BrightSuite on March 4, 2026. BrightSuite subsidiaries include VectorMail.", "https://ir.cloudnorth.test/brightsuite"),
            s("VectorMail support page", "VectorMail is part of BrightSuite. Account billing migrates to CloudNorth in May 2026.", "https://support.vectormail.test/owner"),
        ],
        "intent": "recent",
        "domain": "company",
        "must_have": ["cloudnorth", "brightsuite", "vectormail"],
        "nice_have": ["march 4, 2026", "2023", "acquisition"],
        "forbidden": [
            "brightsuite owns vectormail now",
            "currently owned by brightsuite",
            "vectormail is currently owned by brightsuite",
            "independent",
        ],
    },
    {
        "id": "requested_csv_shape",
        "query": "Return CSV columns: Product,Monthly,Annual for StreamLite and ViewMax annual costs.",
        "desc": "Format compliance plus annual cost math",
        "snippets": [
            s("StreamLite pricing", "StreamLite costs $9 per month for the standard plan.", "https://streamlite.example.test/pricing"),
            s("ViewMax pricing", "ViewMax costs $14 per month for the standard plan.", "https://viewmax.example.test/pricing"),
        ],
        "intent": "search",
        "domain": "pricing",
        "format_hint": "csv",
        "must_have": ["Product,Monthly,Annual", "StreamLite", "ViewMax", "108", "168"],
        "nice_have": ["9", "14"],
        "forbidden": ["```", "Winner", "explanation"],
        "csv_header": "Product,Monthly,Annual",
    },
    {
        "id": "temporal_before_constraint",
        "query": "Before the June 2026 recall, what battery issue did AeroScoot report?",
        "desc": "Respect before constraint and ignore later recall details",
        "snippets": [
            s("AeroScoot April service bulletin", "In April 2026, AeroScoot reported intermittent battery swelling in humid conditions affecting model A7 packs.", "https://support.aeroscoot.test/april", "2026-04-17"),
            s("AeroScoot June recall", "In June 2026, AeroScoot recalled A7 scooters after two battery fire incidents.", "https://safety.example.test/aeroscoot-june", "2026-06-12"),
            s("AeroScoot March FAQ", "The company said charging speed complaints were unrelated to battery safety.", "https://support.aeroscoot.test/march", "2026-03-29"),
        ],
        "intent": "recent",
        "domain": "product",
        "must_have": ["battery swelling", "humid", "april 2026"],
        "nice_have": ["a7"],
        "forbidden": ["fire", "recall", "june"],
    },
]


def score(answer: str, case: dict) -> tuple[int, list[str]]:
    low = answer.lower()
    stripped = answer.strip()
    notes: list[str] = []
    total = 0

    for kw in case["must_have"]:
        if kw.lower() in low:
            total += 3
        else:
            notes.append(f"MISSING must_have: {kw!r}")

    for kw in case.get("nice_have", []):
        if kw.lower() in low:
            total += 1

    for phrase in case.get("forbidden", []):
        if phrase.lower() in low:
            total -= 5
            notes.append(f"FORBIDDEN: {phrase!r}")

    if any(marker in low for marker in ("as an ai", "i think", "i cannot access")):
        total -= 4
        notes.append("BAD MODEL META")

    words = answer.split()
    if len(words) <= 3:
        total -= 3
        notes.append(f"TOO TERSE ({len(words)} words)")
    if len(words) > 180:
        total -= 2
        notes.append(f"BLOATED ({len(words)} words)")

    if case.get("csv_header"):
        first_line = stripped.splitlines()[0].strip() if stripped else ""
        if first_line != case["csv_header"]:
            total -= 4
            notes.append(f"BAD CSV HEADER: {first_line!r}")

    return total, notes


def run() -> None:
    results: dict[str, dict] = {m: {"total": 0, "elapsed": 0.0, "cases": []} for m in CANDIDATES}

    for case in CASES:
        print("\n" + "=" * 80)
        print(f"CASE: {case['id']} | {case['desc']}")
        print(f"Query: {case['query']}")
        print("-" * 80)

        for model in CANDIDATES:
            t0 = time.time()
            try:
                answer = summarize_with_model(
                    query=case["query"],
                    items=case["snippets"],
                    intent=case.get("intent", ""),
                    domain=case.get("domain", ""),
                    format_hint=case.get("format_hint"),
                    model=model,
                    timeout_s=60.0,
                )
            except Exception as exc:
                answer = f"[ERROR: {exc}]"
            elapsed = time.time() - t0

            sc, notes = score(answer, case)
            results[model]["total"] += sc
            results[model]["elapsed"] += elapsed
            results[model]["cases"].append(
                {
                    "id": case["id"],
                    "score": sc,
                    "elapsed": round(elapsed, 2),
                    "answer": answer,
                    "notes": notes,
                }
            )

            flag = "PASS" if sc > 0 and not notes else "CHECK"
            print(f"\n[{flag}] {model:12s} score={sc:+d} elapsed={elapsed:.2f}s")
            print(textwrap.indent(textwrap.fill(answer, 96), "  "))
            for note in notes:
                print(f"  >> {note}")

    print("\n" + "=" * 80)
    print("LEADERBOARD")
    print("-" * 80)
    ranked = sorted(CANDIDATES, key=lambda m: results[m]["total"], reverse=True)
    for i, model in enumerate(ranked, 1):
        avg = results[model]["elapsed"] / len(CASES)
        print(f"#{i} {model:12s} total={results[model]['total']:+d} avg_latency={avg:.2f}s")

    out_path = Path(__file__).parent / "hard_summarizer_gemma_qwen_results.json"
    with open(out_path, "w") as f:
        json.dump({"candidates": CANDIDATES, "cases": [c["id"] for c in CASES], "results": results}, f, indent=2)
    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    run()
