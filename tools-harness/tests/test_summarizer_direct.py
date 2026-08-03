#!/usr/bin/env python3
"""
Test summarizer DIRECTLY with controlled evidence.
Verify gemma4 uses ONLY evidence vs training data.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# Force fresh import
import importlib
import tools.search_agentic as sa
importlib.reload(sa)

from tools.search_result import SearchSnippet

def test_python_version():
    """Test: does summarizer return 3.14.2 when it's in evidence?"""
    print("TEST 1: Python Version")
    print("=" * 80)
    
    # Evidence contains 3.14.2 (correct) and 3.13.1 (stale)
    items = [
        SearchSnippet(
            title="Python 3.14.2 Released",
            snippet="Python 3.14.2 was released on April 25, 2026. This is the latest stable release.",
            url="https://blog.python.org/2026/04/python-3142-released.html",
            published_date="2026-04-25",
            source="exa",
        ),
        SearchSnippet(
            title="Python 3.13.1 Download",
            snippet="Python 3.13.1 is available for download. This was the previous stable release.",
            url="https://www.python.org/downloads/release/python-3131/",
            published_date="2025-12-03",
            source="exa",
        ),
        SearchSnippet(
            title="Wikipedia - Python (programming language)",
            snippet="Python is a high-level programming language. The latest stable release is Python 3.13.1 (December 2025).",
            url="https://en.wikipedia.org/wiki/Python_(programming_language)",
            published_date="2025-12-01",
            source="discovery",
        ),
    ]
    
    query = "What is the latest stable Python release?"
    
    print(f"Query: {query}")
    print(f"Evidence items: {len(items)}")
    for i, item in enumerate(items):
        print(f"  [{i}] {item.title} | {item.published_date}")
        print(f"      Snippet: {item.snippet[:100]}")
    
    print("\nCalling summarize_with_model...")
    result = sa.summarize_with_model(query, items, intent="tech", domain="code")
    
    print(f"\nRESULT: {result}")
    print(f"PASS: {'3.14' in result and '3.13' not in result}")
    return result

def test_starship():
    """Test: does summarizer return 2026 date when in evidence?"""
    print("\n\nTEST 2: Starship Test Date")
    print("=" * 80)
    
    items = [
        SearchSnippet(
            title="SpaceX Starship Flight 9 - April 2026",
            snippet="SpaceX successfully launched Starship Flight 9 on April 14, 2026 from Boca Chica. This was the latest test flight.",
            url="https://www.spacex.com/starship/flight-9",
            published_date="2026-04-14",
            source="exa",
        ),
        SearchSnippet(
            title="Starship Flight 8 March 2025",
            snippet="SpaceX launched Starship Flight 8 on March 6, 2025. The vehicle successfully landed back at the pad.",
            url="https://en.wikipedia.org/wiki/Starship",
            published_date="2025-03-06",
            source="discovery",
        ),
    ]
    
    query = "When did the latest SpaceX Starship test happen?"
    
    print(f"Query: {query}")
    print(f"Evidence items: {len(items)}")
    for i, item in enumerate(items):
        print(f"  [{i}] {item.title} | {item.published_date}")
    
    print("\nCalling summarize_with_model...")
    result = sa.summarize_with_model(query, items, intent="recent", domain="news")
    
    print(f"\nRESULT: {result}")
    print(f"PASS: {'2026' in result and 'March 2025' not in result}")
    return result

def test_f1_race():
    """Test: does summarizer extract next F1 race from evidence?"""
    print("\n\nTEST 3: Next F1 Race")
    print("=" * 80)
    
    items = [
        SearchSnippet(
            title="2026 Formula 1 Calendar - Miami Grand Prix",
            snippet="The next Formula 1 race is the Miami Grand Prix on May 3, 2026 at Miami International Autodrome.",
            url="https://www.formula1.com/en/racing/2026",
            published_date="2026-04-28",
            source="exa",
        ),
        SearchSnippet(
            title="Wikipedia - 2026 Formula One World Championship",
            snippet="The 2026 Formula One World Championship is the 77th season. The season started in March 2026.",
            url="https://en.wikipedia.org/wiki/2026_Formula_One_World_Championship",
            published_date="2026-01-01",
            source="discovery",
        ),
    ]
    
    query = "When is the next Formula 1 race?"
    
    print(f"Query: {query}")
    print(f"Evidence items: {len(items)}")
    for i, item in enumerate(items):
        print(f"  [{i}] {item.title} | {item.published_date}")
    
    print("\nCalling summarize_with_model...")
    result = sa.summarize_with_model(query, items, intent="recent", domain="sports")
    
    print(f"\nRESULT: {result}")
    print(f"PASS: {'May 3' in result or 'Miami' in result}")
    return result

if __name__ == "__main__":
    print("SUMMARIZER DIRECT TEST")
    print("=" * 80)
    print("Testing if gemma4 uses ONLY evidence or falls back to training data\n")
    
    r1 = test_python_version()
    r2 = test_starship()
    r3 = test_f1_race()
    
    print("\n\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Test 1 (Python): {'PASS' if '3.14' in r1 else 'FAIL'}")
    print(f"Test 2 (Starship): {'PASS' if '2026' in r2 and 'March 2025' not in r2 else 'FAIL'}")
    print(f"Test 3 (F1): {'PASS' if 'May 3' in r3 or 'Miami' in r3 else 'FAIL'}")


# Integration: requires real Ollama/network/creds/server — auto-skipped when deps down.
import pytest as _pytest
pytestmark = _pytest.mark.live
