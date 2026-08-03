#!/usr/bin/env python3
"""
Information Correctness Test — Verify factual accuracy of mistral-nemo responses.
Tests factual queries, code examples, and knowledge verification.
"""

import asyncio
import time
import json
from typing import Any

# Test cases with known correct answers
TEST_CASES = [
    {
        "query": "What is the capital of France?",
        "category": "geography",
        "expected_keywords": ["Paris"],
        "should_not_contain": ["London", "Berlin", "Madrid"],
    },
    {
        "query": "In what year did World War 2 end?",
        "category": "history",
        "expected_keywords": ["1945"],
        "should_not_contain": ["1944", "1946", "1943"],
    },
    {
        "query": "What is 2 + 2?",
        "category": "math",
        "expected_keywords": ["4"],
        "should_not_contain": ["5", "3", "6"],
    },
    {
        "query": "Is Python a compiled or interpreted language?",
        "category": "programming",
        "expected_keywords": ["interpreted"],
        "should_not_contain": ["compiled"],
    },
    {
        "query": "What is the chemical symbol for Gold?",
        "category": "science",
        "expected_keywords": ["Au"],
        "should_not_contain": ["Go", "Gd", "Ga"],
    },
    {
        "query": "How many continents are there?",
        "category": "geography",
        "expected_keywords": ["7", "six", "eight"],  # Varies by definition
        "should_not_contain": ["15", "3"],
    },
    {
        "query": "What is the freezing point of water in Celsius?",
        "category": "science",
        "expected_keywords": ["0"],
        "should_not_contain": ["32", "100"],
    },
    {
        "query": "Who wrote Romeo and Juliet?",
        "category": "literature",
        "expected_keywords": ["Shakespeare"],
        "should_not_contain": ["Marlowe", "Moliere"],
    },
    {
        "query": "What does HTTP stand for?",
        "category": "technology",
        "expected_keywords": ["HyperText", "Transfer", "Protocol"],
        "should_not_contain": ["HyperTape"],
    },
    {
        "query": "What is the largest planet in our solar system?",
        "category": "astronomy",
        "expected_keywords": ["Jupiter"],
        "should_not_contain": ["Saturn", "Neptune"],
    },
]


def evaluate_response_correctness(response: str, test_case: dict) -> dict[str, Any]:
    """
    Evaluate response correctness based on expected keywords and forbidden content.
    Returns correctness score and details.
    """
    response_lower = response.lower()

    # Check for expected keywords
    keywords_found = []
    for keyword in test_case.get("expected_keywords", []):
        if keyword.lower() in response_lower:
            keywords_found.append(keyword)

    # Check for forbidden content
    forbidden_found = []
    for forbidden in test_case.get("should_not_contain", []):
        if forbidden.lower() in response_lower:
            forbidden_found.append(forbidden)

    # Calculate correctness
    expected_count = len(test_case.get("expected_keywords", []))
    found_count = len(keywords_found)

    if expected_count > 0:
        keyword_score = found_count / expected_count
    else:
        keyword_score = 1.0

    # Penalty for forbidden content
    if forbidden_found:
        correctness_score = max(0, keyword_score - 0.3)
    else:
        correctness_score = keyword_score

    return {
        "correct": correctness_score >= 0.7,
        "score": round(correctness_score, 2),
        "keywords_found": keywords_found,
        "keywords_missing": [k for k in test_case.get("expected_keywords", []) if k not in keywords_found],
        "forbidden_found": forbidden_found,
        "response_length": len(response),
    }


async def query_model_direct(model: str, query: str, timeout_s: float = 15.0) -> str:
    """Query model directly via Ollama."""
    import ollama

    try:
        response_text = ""
        for chunk in ollama.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful AI assistant. Answer questions directly and accurately. Be concise.",
                },
                {"role": "user", "content": query},
            ],
            options={"temperature": 0.3, "top_p": 0.9},
            think=False,
            stream=True,
        ):
            text = chunk.message.content or ""
            if text:
                response_text += text
        return response_text
    except Exception as e:
        return f"ERROR: {str(e)}"


async def evaluate_correctness():
    """Test information correctness across test cases."""

    print("="*80)
    print("INFORMATION CORRECTNESS TEST — MISTRAL-NEMO")
    print("="*80)

    model = "mistral-nemo"
    results = []
    correct_count = 0
    total_count = len(TEST_CASES)

    for i, test_case in enumerate(TEST_CASES, 1):
        query = test_case["query"]
        category = test_case["category"]

        print(f"\n{'─'*80}")
        print(f"[{i}/{total_count}] {category.upper()}")
        print(f"Query: {query}")
        print(f"{'─'*80}")

        # Get response from model
        response = await query_model_direct(model, query)

        # Evaluate correctness
        correctness = evaluate_response_correctness(response, test_case)

        result = {
            "query": query,
            "category": category,
            "response": response[:150],
            "response_length": len(response),
            **correctness,
        }
        results.append(result)

        # Print results
        status = "✅ CORRECT" if correctness["correct"] else "❌ INCORRECT"
        print(f"\nStatus: {status} (Score: {correctness['score']}/1.0)")
        print(f"Response preview: {response[:100]}...")

        if correctness["keywords_found"]:
            print(f"Keywords found: {', '.join(correctness['keywords_found'])}")
        if correctness["keywords_missing"]:
            print(f"Keywords missing: {', '.join(correctness['keywords_missing'])}")
        if correctness["forbidden_found"]:
            print(f"⚠️ Forbidden content found: {', '.join(correctness['forbidden_found'])}")

        if correctness["correct"]:
            correct_count += 1

    # Summary
    print(f"\n\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}\n")

    accuracy = (correct_count / total_count * 100) if total_count > 0 else 0
    print(f"{model.upper()} Information Correctness:")
    print(f"  Correct: {correct_count}/{total_count}")
    print(f"  Accuracy: {accuracy:.1f}%")
    print(f"  Avg response length: {sum(r['response_length'] for r in results) / len(results):.0f} chars")

    # Breakdown by category
    categories = {}
    for result in results:
        cat = result["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "correct": 0}
        categories[cat]["total"] += 1
        if result["correct"]:
            categories[cat]["correct"] += 1

    print(f"\nBy Category:")
    for cat in sorted(categories.keys()):
        stats = categories[cat]
        cat_accuracy = (stats["correct"] / stats["total"] * 100) if stats["total"] > 0 else 0
        print(f"  {cat.capitalize()}: {stats['correct']}/{stats['total']} ({cat_accuracy:.0f}%)")

    # Detailed results
    print(f"\n{'─'*80}")
    print("DETAILED RESULTS")
    print(f"{'─'*80}\n")

    for i, result in enumerate(TEST_CASES, 1):
        status = "✅" if results[i-1]["correct"] else "❌"
        print(f"{status} [{i}] {result['query']}")
        print(f"   Score: {results[i-1]['score']}/1.0")

    print(f"\n{'='*80}")

    # Save results
    with open("information_correctness.json", "w") as f:
        json.dump(
            {
                "model": model,
                "accuracy": accuracy,
                "correct": correct_count,
                "total": total_count,
                "results": results,
            },
            f,
            indent=2,
        )
    print(f"\n✅ Results saved to information_correctness.json")


if __name__ == "__main__":
    asyncio.run(evaluate_correctness())
