"""Test Ornith on clixen-style tasks: tool calling, code gen, classification, planning."""

import json
import re
import time

import ollama

MODEL = "ornith:9b"

def _ask(prompt, system=None, tools=None, options=None):
    opts = dict(options or {})
    opts.setdefault("num_predict", 4096)
    opts.setdefault("temperature", 0.3)
    opts.setdefault("num_ctx", 16384)

    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})

    start = time.time()
    resp = ollama.chat(model=MODEL, messages=msgs, tools=tools, options=opts)
    elapsed = time.time() - start
    return resp, elapsed


def test_tool_calling():
    """Can Ornith emit proper function calls?"""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web for recent information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"}
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from disk",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"}
                    },
                    "required": ["path"],
                },
            },
        },
    ]

    resp, elapsed = _ask(
        "What's the latest price of Bitcoin? Search the web.",
        system="You are an assistant that uses tools. Call web_search when you need current data.",
        tools=tools,
    )

    has_tc = bool(resp.message.tool_calls)
    fn_names = [c.function.name for c in (resp.message.tool_calls or [])]
    print(f"[tool_calling] tool_calls={has_tc} fns={fn_names} elapsed={elapsed:.1f}s")
    assert has_tc, f"Expected tool call, got: {resp.message.content[:200]}"
    assert "web_search" in fn_names, f"Expected web_search, got: {fn_names}"
    print("  PASS")


def test_code_generation():
    """Can Ornith generate correct code?"""
    resp, elapsed = _ask(
        "Write a Python function that takes a list of file paths, "
        "reads each one, counts lines of code (non-blank, non-comment), "
        "and returns a dict of {filename: loc}. Include type hints."
    )
    content = resp.message.content
    has_code = "```" in content or "def " in content
    has_hints = ":" in content and ("str" in content or "List" in content or "dict" in content)
    print(f"[code_gen] has_code={has_code} has_hints={has_hints} elapsed={elapsed:.1f}s")
    assert has_code, f"No code found in: {content[:200]}"
    print(f"  Output:\n{content[:500]}\n  PASS")


def test_classification():
    """Can Ornith classify user intent like clixen's router?"""
    test_cases = [
        ("what's the weather in Boston?", "temporal"),
        ("write a python script to resize images", "code"),
        ("search for restaurants near me", "temporal"),
        ("read my resume.pdf and summarize it", "filesystem"),
        ("implement a binary search tree in rust", "code_heavy"),
        ("what did I ask you yesterday?", "casual"),
        ("create a new directory called projects", "filesystem"),
    ]

    sys_prompt = (
        "Classify the user message into one intent. "
        "Valid intents: casual, code, code_heavy, temporal, filesystem, email, browser, math, automation. "
        "Reply with ONLY the intent word, nothing else."
    )

    passed = 0
    for msg, expected in test_cases:
        resp, elapsed = _ask(msg, system=sys_prompt, options={"num_predict": 64})
        got = resp.message.content.strip().lower().split("\n")[0].strip()
        ok = got == expected or got.startswith(expected)
        status = "OK" if ok else "MIS"
        print(f"  [{status}] '{msg[:40]}' -> '{got}' (expected '{expected}')")
        if ok:
            passed += 1

    print(f"[classification] {passed}/{len(test_cases)} passed")
    assert passed >= 5, f"Only {passed}/{len(test_cases)} classified correctly"


def test_planning():
    """Can Ornith plan a multi-step task?"""
    resp, elapsed = _ask(
        "Plan the steps to: find the largest .jpg files in ~/Pictures, "
        "resize them to 1920px wide, and create a report of what was resized. "
        "List the steps in order with the tools/commands each step needs."
    )
    content = resp.message.content
    has_steps = bool(re.search(r"(?:step|1\.|\d+\.)", content, re.I))
    print(f"[planning] has_steps={has_steps} elapsed={elapsed:.1f}s")
    assert has_steps, f"No steps found in: {content[:300]}"
    print(f"  Output:\n{content[:600]}\n  PASS")


def test_doc_understanding():
    """Can Ornith understand document/form processing?"""
    resp, elapsed = _ask(
        "I have a PDF with form fields: Full_Name (text), Date_of_Birth (date), "
        "Consent (checkbox), Country (dropdown with options: US, CA, MX). "
        "Write Python code using pdfplumber or similar to detect and fill these fields."
    )
    content = resp.message.content
    has_lib = "pdfplumber" in content or "fill" in content or "form" in content
    has_code = "```" in content
    print(f"[doc_understanding] has_lib={has_lib} has_code={has_code} elapsed={elapsed:.1f}s")
    print(f"  Output:\n{content[:500]}\n  PASS")


def test_agentic_reasoning():
    """Can Ornith reason about a tool result and decide next action?"""
    resp, elapsed = _ask(
        "You called web_search with query='current CEO of OpenAI 2026'. "
        "The result was: [search_failed timeout]. "
        "What do you do now? Give one specific next action."
    )
    content = resp.message.content.lower()
    has_retry = "retry" in content or "search" in content or "try again" in content
    has_honest = "could not" in content or "unable" in content or "not find" in content
    print(f"[agentic_reasoning] retry={has_retry} honest={has_honest} elapsed={elapsed:.1f}s")
    assert has_retry or has_honest, f"No sensible next action: {content[:300]}"
    print(f"  Output:\n{resp.message.content[:400]}\n  PASS")


if __name__ == "__main__":
    print(f"=== Testing {MODEL} on clixen-style tasks ===\n")

    tests = [
        ("Tool Calling", test_tool_calling),
        ("Code Generation", test_code_generation),
        ("Intent Classification", test_classification),
        ("Multi-step Planning", test_planning),
        ("Document Understanding", test_doc_understanding),
        ("Agentic Reasoning", test_agentic_reasoning),
    ]

    results = []
    for name, fn in tests:
        print(f"\n--- {name} ---")
        try:
            fn()
            results.append((name, "PASS"))
        except AssertionError as e:
            print(f"  FAIL: {e}")
            results.append((name, "FAIL"))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append((name, "ERROR"))

    print(f"\n=== RESULTS ===")
    for name, status in results:
        print(f"  {status:6s} {name}")
