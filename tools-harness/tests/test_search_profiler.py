#!/usr/bin/env python3
"""
Search agent profiler — detailed stage breakdown with both summarizers.
Shows exactly where time is spent and which model wins end-to-end.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import asyncio
import time
import json
from typing import Any

TEST_QUERIES = [
    "best VPN 2026",
    "bitcoin price today",
    "python tutorial",
]


async def profile_search_with_summarizer(query: str, summarizer_model: str) -> dict[str, Any]:
    """Profile search query execution with timing breakdown."""
    import time
    from agents.search_graph import stream_search_graph
    import tools.search_agentic as search_agentic_module

    # Patch the summarizer
    original_summarize = search_agentic_module.summarize_with_model

    def patched_summarize(q, items, intent="", domain="", timeout_s=40.0, on_token=None):
        import ollama
        from concurrent.futures import ThreadPoolExecutor
        import contextvars

        today_str = "2026-04-14"
        temporal_note = (
            f"Current date: {today_str}. The user is asking about current/live information.\n"
            "Prioritize the most recent evidence. If evidence is older than 7 days, say so.\n"
        ) if intent in ("live_data", "temporal") else ""

        prompt = (
            "You are a search result synthesizer.\n"
            + temporal_note
            + "Answer the user's question using ONLY the evidence below.\n"
            "Rules:\n"
            "- Return one short exact answer, max 2 sentences.\n"
            "- If evidence is weak or conflicting, say that plainly.\n"
            "- Do not invent facts.\n"
            f"Question: {q}\n\nEvidence:\n{search_agentic_module._evidence_block(items)}"
        )

        if on_token:
            def _stream_with_token() -> str:
                prefix_found = False
                buf = ""
                content_parts = []
                for chunk in ollama.chat(
                    model=summarizer_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "Return only the final answer.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    options={"temperature": 0.3, "top_p": 0.9},
                    think=False,
                    stream=True,
                ):
                    text = chunk.message.content or ""
                    if text:
                        content_parts.append(text)
                        if on_token:
                            on_token(text)
                return "".join(content_parts)

            _ex = ThreadPoolExecutor(max_workers=1)
            _f = _ex.submit(contextvars.copy_context().run, _stream_with_token)
            try:
                raw = _f.result(timeout=timeout_s)
            except:
                _ex.shutdown(wait=False)
                return ""
            _ex.shutdown(wait=False)
            return search_agentic_module._extract_answer(raw) if raw else ""
        else:
            resp = ollama.chat(
                model=summarizer_model,
                messages=[
                    {
                        "role": "system",
                        "content": "Return only the final answer.",
                    },
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": 0.3, "top_p": 0.9},
                think=False,
                stream=False,
            )
            return search_agentic_module._extract_answer(resp.message.content or "")

    search_agentic_module.summarize_with_model = patched_summarize

    # Profile stages
    stage_times = {}
    last_stage = {}
    start_total = time.time()

    try:
        for event in stream_search_graph(query, "search", "general"):
            evt_type = event.get("type", "")

            if evt_type == "stage":
                stage_name = event.get("stage", "")
                current_time = time.time()

                # Record end of previous stage
                if last_stage:
                    prev_stage = last_stage["name"]
                    elapsed = current_time - last_stage["time"]
                    if prev_stage not in stage_times:
                        stage_times[prev_stage] = elapsed
                    else:
                        stage_times[prev_stage] += elapsed

                last_stage = {"name": stage_name, "time": current_time}
    except Exception as e:
        pass

    total_time = time.time() - start_total

    # Restore
    search_agentic_module.summarize_with_model = original_summarize

    return {
        "query": query,
        "summarizer": summarizer_model,
        "total_time": round(total_time, 2),
        "stages": {k: round(v, 2) for k, v in stage_times.items()},
    }


async def run_profiler():
    """Profile all queries with both summarizers."""

    print("="*80)
    print("SEARCH AGENT PROFILER: GEMMA4 VS MISTRAL-NEMO SUMMARIZERS")
    print("="*80)

    models = ["gemma4", "mistral-nemo"]
    all_results = {}

    for model in models:
        print(f"\n{'='*80}")
        print(f"Testing with {model.upper()} summarizer")
        print(f"{'='*80}\n")

        results = []
        for i, query in enumerate(TEST_QUERIES, 1):
            print(f"[{i}/{len(TEST_QUERIES)}] {query}...")
            result = await profile_search_with_summarizer(query, model)
            results.append(result)
            print(f"  Total: {result['total_time']}s")
            print(f"  Stages: {result['stages']}")

        all_results[model] = results

        # Summary for this model
        total_times = [r["total_time"] for r in results]
        avg_time = sum(total_times) / len(total_times) if total_times else 0
        print(f"\n{model.upper()} Summary:")
        print(f"  Average total time: {avg_time:.2f}s")
        print(f"  Min: {min(total_times):.2f}s, Max: {max(total_times):.2f}s")

    # Final comparison
    print(f"\n\n{'='*80}")
    print("FINAL VERDICT")
    print(f"{'='*80}\n")

    g4_results = all_results["gemma4"]
    nemo_results = all_results["mistral-nemo"]

    g4_times = [r["total_time"] for r in g4_results]
    nemo_times = [r["total_time"] for r in nemo_results]

    g4_avg = sum(g4_times) / len(g4_times)
    nemo_avg = sum(nemo_times) / len(nemo_times)

    print(f"GEMMA4:       {g4_avg:.2f}s average")
    print(f"MISTRAL-NEMO: {nemo_avg:.2f}s average")

    if g4_avg < nemo_avg:
        improvement = (g4_avg / nemo_avg - 1) * -100
        winner = "GEMMA4"
    else:
        improvement = (nemo_avg / g4_avg - 1) * 100
        winner = "MISTRAL-NEMO"

    print(f"\n⚡ WINNER: {winner} ({abs(improvement):.0f}% {'faster' if improvement > 0 else 'slower'})")

    # Save results
    with open("search_profiler_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n✅ Detailed results saved to search_profiler_results.json")


if __name__ == "__main__":
    asyncio.run(run_profiler())


# Integration: requires real Ollama/network/creds/server — auto-skipped when deps down.
import pytest as _pytest
pytestmark = _pytest.mark.live
