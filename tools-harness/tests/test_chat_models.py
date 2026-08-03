#!/usr/bin/env python3
"""
Direct chat model comparison: gemma4 vs mistral-nemo
Tests smartness, speed, and quality for general chat (not search agent).
"""

import asyncio
import time
import json
from typing import Any

# Test queries across different chat intents
TEST_CASES = [
    {
        "query": "What is the capital of France?",
        "intent": "factual",
        "expected_keywords": ["Paris", "France"],
    },
    {
        "query": "Explain quantum computing in simple terms",
        "intent": "explanation",
        "expected_keywords": ["quantum", "computing", "bits", "qubits"],
    },
    {
        "query": "Write a short haiku about spring",
        "intent": "creative",
        "expected_keywords": ["spring", "haiku", "poetry"],
    },
    {
        "query": "How do I fix a leaky faucet?",
        "intent": "how_to",
        "expected_keywords": ["faucet", "fix", "water", "wrench"],
    },
    {
        "query": "What's 15% of 280?",
        "intent": "math",
        "expected_keywords": ["42", "percent", "calculation"],
    },
    {
        "query": "Tell me about the history of the internet",
        "intent": "informational",
        "expected_keywords": ["internet", "history", "ARPANET", "TCP/IP"],
    },
    {
        "query": "Debate: is pineapple on pizza acceptable?",
        "intent": "opinion",
        "expected_keywords": ["pizza", "opinion", "pineapple"],
    },
    {
        "query": "Debug this: my code prints 'hello' twice but should print once",
        "intent": "code_help",
        "expected_keywords": ["loop", "print", "debug"],
    },
]


async def run_model_direct(model: str, query: str) -> dict[str, Any]:
    """Test model directly via Ollama chat API."""
    import ollama

    start = time.time()
    response_text = ""
    token_count = 0

    try:
        for chunk in ollama.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful, intelligent assistant. Be concise and direct."
                },
                {"role": "user", "content": query}
            ],
            options={"temperature": 0.7, "top_p": 0.9},
            think=False,
            stream=True,
        ):
            text = chunk.message.content or ""
            if text:
                response_text += text
                token_count += 1
    except Exception as e:
        return {
            "model": model,
            "query": query,
            "error": str(e),
            "latency": time.time() - start,
        }

    elapsed = time.time() - start

    # Analyze response
    response_lower = response_text.lower()

    return {
        "model": model,
        "query": query,
        "response": response_text[:200],  # First 200 chars
        "response_length": len(response_text),
        "latency_sec": round(elapsed, 2),
        "token_count": token_count,
    }


async def evaluate_models():
    """Compare gemma4 and mistral-nemo on all test cases."""

    print("="*80)
    print("GEMMA4 VS MISTRAL-NEMO: DIRECT CHAT COMPARISON")
    print("="*80)

    models = ["gemma4", "mistral-nemo"]
    results_by_model = {m: [] for m in models}

    for i, test_case in enumerate(TEST_CASES, 1):
        query = test_case["query"]
        intent = test_case["intent"]

        print(f"\n{'─'*80}")
        print(f"[{i}/{len(TEST_CASES)}] {intent.upper()}")
        print(f"Query: {query}")
        print(f"{'─'*80}")

        for model in models:
            result = await run_model_direct(model, query)
            results_by_model[model].append(result)

            if "error" in result:
                print(f"\n  ❌ {model}: ERROR — {result['error']}")
            else:
                response_preview = result["response"].replace("\n", " ")[:70]
                print(f"\n  {model}:")
                print(f"    Latency: {result['latency_sec']}s")
                print(f"    Length: {result['response_length']} chars")
                print(f"    Response: \"{response_preview}...\"")

    # Summary statistics
    print(f"\n\n{'='*80}")
    print("SUMMARY STATISTICS")
    print(f"{'='*80}\n")

    for model in models:
        results = results_by_model[model]
        failed = sum(1 for r in results if "error" in r)
        latencies = [r["latency_sec"] for r in results if "latency_sec" in r]
        lengths = [r["response_length"] for r in results if "response_length" in r]

        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            min_latency = min(latencies)
            max_latency = max(latencies)
        else:
            avg_latency = min_latency = max_latency = 0

        if lengths:
            avg_length = sum(lengths) / len(lengths)
        else:
            avg_length = 0

        print(f"{model.upper()}:")
        print(f"  Success rate: {len(results) - failed}/{len(results)} ({((len(results)-failed)*100//len(results))}%)")
        if latencies:
            print(f"  Latency: avg={avg_latency:.2f}s, min={min_latency:.2f}s, max={max_latency:.2f}s")
            print(f"  Avg response length: {avg_length:.0f} chars")
        print()

    # Comparison
    print(f"{'─'*80}")
    print("DETAILED COMPARISON")
    print(f"{'─'*80}\n")

    if results_by_model["gemma4"] and results_by_model["mistral-nemo"]:
        g4_latencies = [r["latency_sec"] for r in results_by_model["gemma4"] if "latency_sec" in r]
        nemo_latencies = [r["latency_sec"] for r in results_by_model["mistral-nemo"] if "latency_sec" in r]

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
        print(f"  - Capability: Advanced (vision, code, math)")

        print(f"\nmistral-nemo:")
        print(f"  - Size: 7.1 GB")
        print(f"  - Avg latency: {nemo_avg:.2f}s")
        print(f"  - Capability: Fast general chat, search synthesis")

    print(f"\n{'='*80}")

    # Save results
    with open("chat_model_comparison.json", "w") as f:
        json.dump(results_by_model, f, indent=2)
    print("\n✅ Results saved to chat_model_comparison.json")


if __name__ == "__main__":
    asyncio.run(evaluate_models())


# Integration: requires real Ollama/network/creds/server — auto-skipped when deps down.
import pytest as _pytest
pytestmark = _pytest.mark.live
