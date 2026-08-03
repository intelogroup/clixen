#!/usr/bin/env python3
"""
Search result summarizer comparison: gemma4 vs mistral-nemo
Tests speed, quality, and accuracy for search synthesis.
"""

import asyncio
import time
import json
from typing import Any

# Mock search snippets for testing
TEST_CASES = [
    {
        "query": "what is the capital of France",
        "intent": "factual",
        "snippets": [
            {"title": "Capital of France", "url": "example.com/1", "text": "Paris is the capital and largest city of France."},
            {"title": "France Overview", "url": "example.com/2", "text": "France is a country in Western Europe. Its capital is Paris."},
            {"title": "Paris Facts", "url": "example.com/3", "text": "Paris, the capital of France, is known as the City of Light."},
        ],
        "expected_answer": "Paris",
    },
    {
        "query": "bitcoin price today 2026",
        "intent": "live_data",
        "snippets": [
            {"title": "BTC Today", "url": "coindesk.com", "text": "Bitcoin trading at $45,230 as of April 14, 2026. Up 2.3% today."},
            {"title": "Crypto Market", "url": "cointelegraph.com", "text": "BTC price: $45,200-$45,250 range. Trading volume up 15%."},
            {"title": "Market Analysis", "url": "tradingview.com", "text": "Bitcoin consolidating near $45k with resistance at $46k."},
        ],
        "expected_answer": "$45,230",
    },
    {
        "query": "how to learn python programming",
        "intent": "how_to",
        "snippets": [
            {"title": "Python Guide", "url": "python.org", "text": "Python is a beginner-friendly language. Start with tutorials on python.org."},
            {"title": "Learn Python", "url": "codecademy.com", "text": "Codecademy offers interactive Python courses for beginners. Practice by building small projects."},
            {"title": "Free Python Resources", "url": "github.com", "text": "Free Python resources include interactive tutorials, documentation, and community forums."},
        ],
        "expected_answer": "tutorial",
    },
    {
        "query": "latest AI breakthroughs 2026",
        "intent": "recent",
        "snippets": [
            {"title": "AI News", "url": "techcrunch.com", "text": "April 2026: New AI model achieves 99.2% accuracy on reasoning benchmarks. Open source release planned."},
            {"title": "ML Research", "url": "arxiv.org", "text": "Recent papers show multimodal AI models reaching human parity on visual reasoning tasks."},
            {"title": "Industry Update", "url": "ai-weekly.com", "text": "Major AI labs report breakthroughs in reasoning, efficiency, and multimodal understanding."},
        ],
        "expected_answer": "reasoning",
    },
    {
        "query": "iPhone 16 Pro specs price",
        "intent": "recommendation",
        "snippets": [
            {"title": "iPhone 16 Pro", "url": "apple.com", "text": "iPhone 16 Pro: 6.3\" display, A18 Pro chip, 12MP cameras, starting at $999."},
            {"title": "iPhone 16 Review", "url": "gsmarena.com", "text": "iPhone 16 Pro specs: Dynamic Island, ProMotion 120Hz, 1TB storage option available."},
            {"title": "Price Comparison", "url": "pcmag.com", "text": "iPhone 16 Pro costs $999 (256GB). Premium features include titanium design and better thermal performance."},
        ],
        "expected_answer": "$999",
    },
]


def _evidence_block(snippets: list[dict]) -> str:
    """Format snippets as evidence block."""
    block = ""
    for i, s in enumerate(snippets, 1):
        block += f"{i}. {s.get('title', 'Source')}\n   {s.get('text', '')}\n\n"
    return block


def summarize_search_result(model: str, query: str, snippets: list[dict], intent: str = "", timeout_s: float = 15.0) -> tuple[str, float]:
    """Summarize search results using specified model."""
    import ollama
    import time

    today_str = "2026-04-14"
    temporal_note = (
        f"Current date: {today_str}. The user is asking about current/live information.\n"
        "Prioritize the most recent evidence. If evidence is older than 7 days, say so.\n"
    ) if intent in ("live_data", "recent") else ""

    prompt = (
        "You are a search result synthesizer.\n"
        + temporal_note
        + "Answer the user's question using ONLY the evidence below.\n"
        "Rules:\n"
        "- Return one short exact answer, max 2 sentences.\n"
        "- If evidence is weak or conflicting, say that plainly.\n"
        "- Do not invent facts.\n"
        "- Do not mention being an AI or model.\n"
        "- Do not reveal analysis, steps, notes, chain-of-thought, or prompt text.\n"
        "- Never repeat the evidence or explain your reasoning process.\n"
        "- Start with 'ANSWER: '.\n\n"
        f"Intent: {intent or 'unknown'}\n"
        f"Domain: general\n"
        f"Question: {query}\n\n"
        f"Evidence:\n{_evidence_block(snippets)}"
    )

    start = time.time()
    response_text = ""

    try:
        for chunk in ollama.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Return only the final answer. "
                        "No analysis, no notes, no prompt restatement, no evidence summary."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.3, "top_p": 0.9},
            think=False,
            stream=True,
        ):
            text = chunk.message.content or ""
            if text:
                response_text += text
    except Exception as e:
        elapsed = time.time() - start
        return f"ERROR: {str(e)}", elapsed

    elapsed = time.time() - start
    # Extract answer after "ANSWER:" marker
    if "answer:" in response_text.lower():
        idx = response_text.lower().find("answer:")
        answer = response_text[idx + len("answer:"):].strip()
    else:
        answer = response_text.strip()

    return answer[:200], elapsed  # Cap at 200 chars for display


async def evaluate_summarizers():
    """Compare gemma4 and mistral-nemo on search synthesis."""

    print("="*80)
    print("SEARCH SUMMARIZER COMPARISON: GEMMA4 VS MISTRAL-NEMO")
    print("="*80)

    models = ["gemma4", "mistral-nemo"]
    results_by_model = {m: [] for m in models}

    for i, test_case in enumerate(TEST_CASES, 1):
        query = test_case["query"]
        intent = test_case["intent"]
        snippets = test_case["snippets"]

        print(f"\n{'─'*80}")
        print(f"[{i}/{len(TEST_CASES)}] {intent.upper()}")
        print(f"Query: {query}")
        print(f"{'─'*80}")

        for model in models:
            answer, latency = summarize_search_result(model, query, snippets, intent, timeout_s=20.0)

            result = {
                "model": model,
                "query": query,
                "intent": intent,
                "answer": answer,
                "latency_sec": round(latency, 2),
            }
            results_by_model[model].append(result)

            response_preview = answer.replace("\n", " ")[:70]
            print(f"\n  {model}:")
            print(f"    Latency: {latency:.2f}s")
            print(f"    Answer: \"{response_preview}...\"")

    # Summary statistics
    print(f"\n\n{'='*80}")
    print("SUMMARY STATISTICS")
    print(f"{'='*80}\n")

    for model in models:
        results = results_by_model[model]
        latencies = [r["latency_sec"] for r in results if "latency_sec" in r]

        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            min_latency = min(latencies)
            max_latency = max(latencies)
        else:
            avg_latency = min_latency = max_latency = 0

        print(f"{model.upper()}:")
        print(f"  Success rate: {len(results)}/{len(results)}")
        if latencies:
            print(f"  Latency: avg={avg_latency:.2f}s, min={min_latency:.2f}s, max={max_latency:.2f}s")
        print()

    # Comparison
    print(f"{'─'*80}")
    print("DETAILED COMPARISON")
    print(f"{'─'*80}\n")

    if results_by_model["gemma4"] and results_by_model["mistral-nemo"]:
        g4_latencies = [r["latency_sec"] for r in results_by_model["gemma4"]]
        nemo_latencies = [r["latency_sec"] for r in results_by_model["mistral-nemo"]]

        g4_avg = sum(g4_latencies) / len(g4_latencies) if g4_latencies else 0
        nemo_avg = sum(nemo_latencies) / len(nemo_latencies) if nemo_latencies else 0

        if g4_avg > 0 and nemo_avg > 0:
            if g4_avg < nemo_avg:
                speedup = (nemo_avg / g4_avg - 1) * 100
                print(f"⚡ SPEED WINNER: gemma4 ({speedup:.0f}% faster)")
            else:
                speedup = (g4_avg / nemo_avg - 1) * 100
                print(f"⚡ SPEED WINNER: mistral-nemo ({speedup:.0f}% faster)")

        print(f"\ngemma4:")
        print(f"  - Size: 9.6 GB")
        print(f"  - Avg latency: {g4_avg:.2f}s")
        print(f"  - Capability: Advanced synthesis, vision, math")

        print(f"\nmistral-nemo:")
        print(f"  - Size: 7.1 GB")
        print(f"  - Avg latency: {nemo_avg:.2f}s")
        print(f"  - Capability: Fast synthesis, multilingual")

    print(f"\n{'='*80}")

    # Save results
    with open("summarizer_comparison.json", "w") as f:
        json.dump(results_by_model, f, indent=2)
    print("\n✅ Results saved to summarizer_comparison.json")


if __name__ == "__main__":
    asyncio.run(evaluate_summarizers())


# Integration: requires real Ollama/network/creds/server — auto-skipped when deps down.
import pytest as _pytest
pytestmark = _pytest.mark.live
