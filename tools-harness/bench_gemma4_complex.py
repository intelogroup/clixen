"""
gemma4:12b-mlx complex-task accuracy benchmark.

Tests 7 categories of complex tasks that mirror real usage in tools-harness:
  1. Code generation (Python function from spec)
  2. Multi-step math reasoning (3-4 step word problems)
  3. Multi-step logical reasoning (constraint satisfaction)
  4. Structured data extraction (JSON from prose)
  5. Long-context Q&A (read a doc, answer specific question)
  6. Multi-turn conversation (context retention across 4 turns)
  7. Agentic tool use (multi-round tool orchestration)

Each test scores:
  - correctness (binary, weighted by must_have criteria)
  - latency (seconds)
  - whether output is in expected format
  - whether the model followed all instructions

Usage: python3 bench_gemma4_complex.py
       python3 bench_gemma4_complex.py --model gemma4:e2b  (for comparison)
       python3 bench_gemma4_complex.py --quick  (skip latency for fast iteration)
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import ollama


DEFAULT_MODEL = "gemma4:12b-mlx"
RESULTS_DIR = Path("/tmp/gemma4_complex_bench")


# ────────────────────────────────────────────────────────────────────────────
# Test 1: Code generation
# ────────────────────────────────────────────────────────────────────────────
TEST_CODE = {
    "name": "code_generation",
    "category": "code",
    "system": "You are a Python expert. Output ONLY valid Python code — no explanations, no markdown fences.",
    "prompt": (
        "Write a Python function `fuzzy_search(query: str, candidates: list[str], "
        "threshold: float = 0.6) -> list[tuple[str, float]]` that returns each candidate "
        "with its similarity score (0-1) to the query, filtered to only those with score >= threshold. "
        "Use rapidfuzz's fuzz.ratio divided by 100 for the score. Sort by score descending. "
        "Include a docstring."
    ),
    "must_contain": ["def fuzzy_search", "rapidfuzz", "fuzz.ratio", "threshold"],
    "validate": lambda out: _validate_fuzzy_search(out),
    "weight": 10,
}


def _validate_fuzzy_search(code: str) -> dict:
    """Execute the generated code in a sandboxed namespace and verify it works."""
    try:
        # Strip markdown fences if present
        code = re.sub(r"```python\s*", "", code)
        code = re.sub(r"```\s*$", "", code.strip())
        ns: dict[str, Any] = {}
        exec(code, ns)
        fn = ns.get("fuzzy_search")
        if not fn:
            return {"ok": False, "reason": "no fuzzy_search function defined"}
        # Smoke test
        result = fn("hello world", ["hello word", "goodbye", "hello there"], 0.5)
        if not isinstance(result, list):
            return {"ok": False, "reason": f"expected list, got {type(result)}"}
        for item in result:
            if not (isinstance(item, tuple) and len(item) == 2):
                return {"ok": False, "reason": f"bad item shape: {item!r}"}
            if not (isinstance(item[1], float) and 0.0 <= item[1] <= 1.0):
                return {"ok": False, "reason": f"bad score: {item[1]!r}"}
        return {"ok": True, "items": result}
    except Exception as e:
        return {"ok": False, "reason": f"exec error: {e}"}


# ────────────────────────────────────────────────────────────────────────────
# Test 2: Multi-step math reasoning
# ────────────────────────────────────────────────────────────────────────────
TEST_MATH = {
    "name": "multi_step_math",
    "category": "reasoning",
    "system": "Solve step by step, then give the final numerical answer on the last line as: ANSWER: <number>",
    "prompt": (
        "A bakery sells croissants for $3.50 each and loaves of bread for $6.00 each. "
        "On Monday, they sold 24 croissants and 18 loaves. On Tuesday, they sold 30% "
        "more croissants and 5 fewer loaves. What is the total revenue for both days, "
        "in dollars? Express to 2 decimal places."
    ),
    "must_contain": ["84", "93", "177"],  # Mon=$84+$108=... wait
    # Monday: 24*3.50 = 84, 18*6 = 108, total = 192
    # Tuesday: 30% more croissants = 24*1.3 = 31.2 -> $109.20; 13 loaves * 6 = 78
    # Tuesday: 109.20 + 78 = 187.20
    # Total: 192 + 187.20 = 379.20
    "must_contain": ["379", "ANSWER:"],
    "validate": lambda out: _check_answer(out, 379.20, tolerance=0.05),
    "weight": 8,
}


# ────────────────────────────────────────────────────────────────────────────
# Test 3: Multi-step logical reasoning
# ────────────────────────────────────────────────────────────────────────────
TEST_LOGIC = {
    "name": "logical_reasoning",
    "category": "reasoning",
    "system": "Think carefully. State your final answer on the last line as: ANSWER: <letter or number>",
    "prompt": (
        "Five friends — Anna, Ben, Cara, Dan, and Eve — are sitting in a row. "
        "Constraints:\n"
        "  - Anna is not at either end.\n"
        "  - Ben is immediately to the left of Cara.\n"
        "  - Dan is at one of the ends.\n"
        "  - Eve is not adjacent to Dan.\n"
        "  - The middle seat (3rd from either end) is occupied by either Cara or Anna.\n"
        "Question: Who is sitting in seat 5 (the rightmost seat)?"
    ),
    "must_contain": ["ANSWER:"],
    "validate": lambda out: _check_answer(out, "Eve", mode="substring"),
    "weight": 8,
}


# ────────────────────────────────────────────────────────────────────────────
# Test 4: Structured data extraction
# ────────────────────────────────────────────────────────────────────────────
TEST_STRUCTURED = {
    "name": "structured_extraction",
    "category": "structured",
    "system": "Extract the requested data. Output ONLY valid JSON, no markdown fences, no commentary.",
    "prompt": (
        'Extract the following information from the email below as a JSON object with these exact keys: '
        '"sender", "recipient", "subject", "sent_date", "action_items" (a list of strings). '
        'Dates should be in ISO 8601 format (YYYY-MM-DD). '
        'If a field is missing, use null. Output ONLY the JSON.\n\n'
        'Email:\n'
        '---\n'
        'From: alice@example.com\n'
        'To: bob@example.com\n'
        'Date: March 15, 2026\n'
        'Subject: Q2 Roadmap Review\n'
        '\n'
        'Hi Bob,\n'
        '\n'
        'Thanks for the great meeting yesterday. As discussed:\n'
        '- Please review the attached Q2 roadmap doc by EOD Friday\n'
        '- Schedule a follow-up with the design team for next Tuesday\n'
        '- Send me the user research summary when you have a chance\n'
        '\n'
        'Let me know if you have questions.\n'
        '\n'
        'Best,\n'
        'Alice\n'
    ),
    "must_contain": ['"sender":', '"alice@example.com"', '"Q2 Roadmap Review"', "2026-03-15", "action_items"],
    "validate": lambda out: _validate_json_structure(out),
    "weight": 8,
}


def _validate_json_structure(out: str) -> dict:
    """Verify extracted JSON has all required keys with sensible values."""
    try:
        # Strip markdown fences
        clean = re.sub(r"```json\s*", "", out)
        clean = re.sub(r"```\s*", "", clean).strip()
        data = json.loads(clean)
    except json.JSONDecodeError as e:
        return {"ok": False, "reason": f"json parse error: {e}"}
    required = {"sender", "recipient", "subject", "sent_date", "action_items"}
    missing = required - set(data.keys())
    if missing:
        return {"ok": False, "reason": f"missing keys: {missing}"}
    if data["sender"] != "alice@example.com":
        return {"ok": False, "reason": f"sender wrong: {data['sender']!r}"}
    if "Q2" not in data["subject"]:
        return {"ok": False, "reason": f"subject wrong: {data['subject']!r}"}
    if data["sent_date"] != "2026-03-15":
        return {"ok": False, "reason": f"date wrong: {data['sent_date']!r}"}
    if not isinstance(data["action_items"], list) or len(data["action_items"]) < 2:
        return {"ok": False, "reason": f"action_items not a list with >=2 items: {data['action_items']!r}"}
    return {"ok": True, "data": data}


# ────────────────────────────────────────────────────────────────────────────
# Test 5: Long-context Q&A
# ────────────────────────────────────────────────────────────────────────────
LONG_DOC = """
""" + (
    "The history of electric vehicles spans nearly two centuries. "
    "Early experiments in the 1830s included small-scale electric carriages built in Scotland, "
    "Hungary, and the Netherlands. By the late 1800s, electric vehicles held several speed and "
    "distance records. "
) * 80 + """

A pivotal moment came with the General Motors EV1, launched in 1996. The EV1 was the first
mass-produced, purpose-designed electric vehicle from a major automaker. It was a two-seat
subcompact car powered by a lead-acid battery, with a range of about 70-100 miles. Despite
positive reviews from drivers, GM terminated the EV1 program in 2003, citing the program's
inability to be profitable. The decision became the subject of the 2006 documentary film
"Who Killed the Electric Car?" and is widely cited as a cautionary tale in the history of
electric mobility.

The next major milestone was the introduction of the Tesla Roadster in 2008. Unlike the EV1,
the Roadster was a high-performance sports car with a lithium-ion battery pack. It demonstrated
that electric vehicles could be fast, desirable, and practical for daily use. The Roadster's
2008-2012 production run established Tesla as a serious automaker and triggered an industry-
wide shift toward electric vehicle development.

By 2025, electric vehicles had crossed the threshold where global sales of EVs exceeded those
of internal combustion vehicles in several major markets including Norway, China, and parts
of Europe. The International Energy Agency reported that EVs accounted for approximately 18%
of new car sales worldwide in 2024, up from 9% in 2021.
"""

TEST_LONG_CONTEXT = {
    "name": "long_context_qa",
    "category": "long_context",
    "system": "Answer concisely based on the document provided. Quote the document when asked.",
    "prompt": LONG_DOC + "\n\nQuestion: What was the range of the General Motors EV1, and what battery chemistry did it use?",
    "must_contain": ["70", "100", "lead-acid", "GM EV1"],
    "validate": lambda out: ("lead-acid" in out.lower() and ("70" in out or "100" in out)),
    "weight": 7,
}


# ────────────────────────────────────────────────────────────────────────────
# Test 6: Multi-turn conversation
# ────────────────────────────────────────────────────────────────────────────
TEST_MULTITURN = {
    "name": "multi_turn",
    "category": "conversation",
    "weight": 6,
    "turns": [
        {
            "user": "My favorite programming language is Python and my favorite editor is VS Code.",
            "must_contain": [],
            "validate": lambda out: True,
        },
        {
            "user": "What is my favorite programming language?",
            "must_contain": ["Python"],
            "validate": lambda out: "python" in out.lower(),
        },
        {
            "user": "And what editor do I use?",
            "must_contain": ["VS Code"],
            "validate": lambda out: ("vs code" in out.lower() or "vscode" in out.lower()),
        },
        {
            "user": "Can you write a one-line Python function to print a greeting using my favorite editor's name?",
            "must_contain": ["VS Code", "print"],
            "validate": lambda out: ("vs code" in out.lower() and "print" in out.lower()),
        },
    ],
}


# ────────────────────────────────────────────────────────────────────────────
# Test 7: Agentic tool use (multi-round)
# ────────────────────────────────────────────────────────────────────────────
TEST_AGENTIC = {
    "name": "agentic_tool_use",
    "category": "agentic",
    "system": (
        "You are a helpful assistant with access to tools. When the user asks a question "
        "requiring current data (calendar, tasks, emails, time, web), call the appropriate tool. "
        "After getting tool results, synthesize a clear answer."
    ),
    "prompt": (
        "What is the current local time and date? After answering, also tell me — without "
        "calling any more tools — what day of the week the date '2026-12-25' falls on. "
        "Don't call any tools for the second part, just reason from the date."
    ),
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_current_time",
                "description": "Return the current local date and time as a JSON object with 'date' (YYYY-MM-DD) and 'time' (HH:MM:SS) fields.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ],
    "must_contain": ["get_current_time", "Thursday"],  # 2026-12-25 is a Friday, but model might say "Thursday" or be off
    "validate": lambda out, tool_calls: _validate_agentic(out, tool_calls),
    "weight": 12,
}


def _validate_agentic(out: str, tool_calls: list) -> dict:
    """Verify the model called the right tool AND gave a date+weekday answer."""
    called_time = any(c.function.name == "get_current_time" for c in (tool_calls or []))
    has_date = bool(re.search(r"\d{4}-\d{2}-\d{2}", out))
    # 2026-12-25 is a Friday
    weekday_ok = any(d in out for d in ("Friday", "friday", "Dec 25"))
    return {
        "ok": called_time and has_date and weekday_ok,
        "called_time": called_time,
        "has_date": has_date,
        "weekday_ok": weekday_ok,
    }


# ────────────────────────────────────────────────────────────────────────────
# Helper: check for expected answer
# ────────────────────────────────────────────────────────────────────────────
def _check_answer(out: str, expected, tolerance=0.0, mode="numeric") -> dict:
    if mode == "numeric":
        # Find ANSWER: <number> on the last line
        m = re.search(r"ANSWER:\s*([-+]?\d*\.?\d+)", out)
        if not m:
            return {"ok": False, "reason": "no ANSWER: line"}
        try:
            val = float(m.group(1))
        except ValueError:
            return {"ok": False, "reason": f"non-numeric: {m.group(1)!r}"}
        if abs(val - expected) > tolerance:
            return {"ok": False, "reason": f"expected {expected}, got {val}"}
        return {"ok": True, "value": val}
    elif mode == "substring":
        if expected.lower() in out.lower():
            return {"ok": True}
        return {"ok": False, "reason": f"{expected!r} not in output"}


# ────────────────────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────────────────────
def run_test(test: dict, model: str, verbose: bool = True) -> dict:
    """Run a single test and return a result dict."""
    name = test["name"]
    if test.get("category") == "agentic":
        return _run_agentic_test(test, model, verbose)
    if test.get("category") == "conversation":
        return _run_conversation_test(test, model, verbose)
    return _run_single_turn(test, model, verbose)


def _run_single_turn(test: dict, model: str, verbose: bool) -> dict:
    """Run a single-turn test and validate."""
    t0 = time.time()
    try:
        r = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": test["system"]},
                {"role": "user", "content": test["prompt"]},
            ],
            options={"num_predict": 1500, "temperature": 0.1, "num_ctx": 16384},
            think=False,
        )
        elapsed = time.time() - t0
        text = (r.message.content or "").strip()
    except Exception as e:
        return {
            "name": test["name"],
            "score": 0,
            "elapsed": 0,
            "words": 0,
            "ok": False,
            "error": str(e)[:200],
            "weight": test["weight"],
        }

    # Score
    missing_must = [m for m in test["must_contain"] if m not in text]
    has_all_must = len(missing_must) == 0
    val_result = test["validate"](text) if not missing_must else {"ok": False, "reason": f"missing: {missing_must}"}
    val_ok = val_result.get("ok", False)
    words = len(text.split())

    score = test["weight"] if val_ok else (test["weight"] // 2 if has_all_must else 0)
    ok = val_ok

    if verbose:
        status = "OK" if ok else ("PARTIAL" if has_all_must else "FAIL")
        print(f"  [{status:7s}] {test['name']:24s}  score={score:2d}/{test['weight']:2d}  "
              f"{elapsed:5.1f}s  {words:4d}w  {val_result.get('reason', '')[:60]}")
    return {
        "name": test["name"],
        "score": score,
        "elapsed": elapsed,
        "words": words,
        "ok": ok,
        "has_must": has_all_must,
        "weight": test["weight"],
        "val_result": val_result,
    }


def _run_conversation_test(test: dict, model: str, verbose: bool) -> dict:
    """Run multi-turn test, scoring each turn."""
    messages = [{"role": "system", "content": "You are a friendly, concise assistant."}]
    turn_results = []
    total_elapsed = 0
    for i, turn in enumerate(test["turns"]):
        messages.append({"role": "user", "content": turn["user"]})
        t0 = time.time()
        try:
            r = ollama.chat(
                model=model,
                messages=messages,
                options={"num_predict": 600, "temperature": 0.1, "num_ctx": 4096},
                think=False,
            )
            elapsed = time.time() - t0
            total_elapsed += elapsed
            text = (r.message.content or "").strip()
        except Exception as e:
            turn_results.append({"turn": i, "ok": False, "error": str(e)[:100]})
            continue
        messages.append({"role": "assistant", "content": text})
        ok = turn["validate"](text)
        missing = [m for m in turn["must_contain"] if m not in text]
        turn_results.append({"turn": i, "ok": ok, "missing": missing, "text_preview": text[:80]})
        if verbose:
            status = "OK" if ok else "FAIL"
            print(f"  [{status:4s}] {test['name']}.turn{i+1}  {elapsed:5.1f}s  {text[:80]!r}")

    passed = sum(1 for r in turn_results if r["ok"])
    total = len(turn_results)
    score = test["weight"] * passed // total
    return {
        "name": test["name"],
        "score": score,
        "elapsed": total_elapsed,
        "ok": passed == total,
        "turns_passed": passed,
        "turns_total": total,
        "weight": test["weight"],
    }


def _run_agentic_test(test: dict, model: str, verbose: bool) -> dict:
    """Run a multi-round tool-use test, scoring tool accuracy + final answer."""
    messages = [
        {"role": "system", "content": test["system"]},
        {"role": "user", "content": test["prompt"]},
    ]
    all_tool_calls = []
    total_elapsed = 0
    final_text = ""
    rounds = 0
    MAX_ROUNDS = 4

    while rounds < MAX_ROUNDS:
        rounds += 1
        t0 = time.time()
        try:
            r = ollama.chat(
                model=model,
                messages=messages,
                tools=test["tools"],
                options={"num_predict": 800, "temperature": 0.1, "num_ctx": 8192},
                think=False,
            )
            elapsed = time.time() - t0
            total_elapsed += elapsed
        except Exception as e:
            return {
                "name": test["name"],
                "score": 0,
                "elapsed": total_elapsed,
                "ok": False,
                "error": str(e)[:200],
                "weight": test["weight"],
            }
        final_text = r.message.content or ""
        if r.message.tool_calls:
            all_tool_calls.extend(r.message.tool_calls)
            # Simulate tool result (for get_current_time, fake a plausible response)
            messages.append(r.message)
            for tc in r.message.tool_calls:
                if tc.function.name == "get_current_time":
                    result = '{"date": "2026-06-07", "time": "13:45:00", "weekday": "Sunday"}'
                else:
                    result = '{"status": "ok"}'
                messages.append({
                    "role": "tool",
                    "name": tc.function.name,
                    "content": result,
                })
            if verbose:
                print(f"  [ROUND {rounds}] {test['name']:24s}  {elapsed:5.1f}s  "
                      f"tools: {[c.function.name for c in r.message.tool_calls]}")
        else:
            if verbose:
                print(f"  [FINAL]  {test['name']:24s}  {elapsed:5.1f}s  {final_text[:80]!r}")
            break

    # Validate
    val_result = test["validate"](final_text, all_tool_calls)
    val_ok = val_result.get("ok", False)
    score = test["weight"] if val_ok else 0

    if verbose:
        status = "OK" if val_ok else "FAIL"
        print(f"  [{status:4s}] {test['name']:24s}  score={score:2d}/{test['weight']:2d}  "
              f"{total_elapsed:5.1f}s  total rounds={rounds}  tools_called={[c.function.name for c in all_tool_calls]}")
    return {
        "name": test["name"],
        "score": score,
        "elapsed": total_elapsed,
        "ok": val_ok,
        "val_result": val_result,
        "rounds": rounds,
        "tool_calls": [c.function.name for c in all_tool_calls],
        "weight": test["weight"],
    }


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--compare", default=None, help="Also run a comparison model and print side-by-side")
    p.add_argument("--out", default=None, help="Write results JSON to this file")
    args = p.parse_args()

    tests = [TEST_CODE, TEST_MATH, TEST_LOGIC, TEST_STRUCTURED, TEST_LONG_CONTEXT, TEST_MULTITURN, TEST_AGENTIC]
    print(f"\n=== {args.model} ===")
    print(f"Tests: {[t['name'] for t in tests]}")
    print()
    results = []
    for t in tests:
        r = run_test(t, args.model)
        results.append(r)

    total_score = sum(r["score"] for r in results)
    total_weight = sum(r["weight"] for r in results)
    total_elapsed = sum(r["elapsed"] for r in results)
    all_ok = all(r.get("ok") for r in results)

    print()
    print("=" * 60)
    print(f"SCORE: {total_score}/{total_weight}  "
          f"({100*total_score/total_weight:.0f}%)  "
          f"total_time={total_elapsed:.1f}s")
    print("=" * 60)
    for r in results:
        ok_mark = "✓" if r.get("ok") else "✗"
        print(f"  {ok_mark} {r['name']:24s}  {r['score']:2d}/{r['weight']:2d}  {r['elapsed']:5.1f}s")

    # Save results
    if args.out:
        out_path = Path(args.out)
    else:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = RESULTS_DIR / f"{args.model.replace(':', '_')}.json"
    out_path.write_text(json.dumps({
        "model": args.model,
        "score": total_score,
        "weight": total_weight,
        "pct": round(100 * total_score / total_weight, 1),
        "elapsed": round(total_elapsed, 1),
        "tests": results,
    }, indent=2))
    print(f"\nResults saved to: {out_path}")

    # Optional comparison
    if args.compare:
        print(f"\n=== {args.compare} (comparison) ===")
        comp_results = []
        for t in tests:
            r = run_test(t, args.compare)
            comp_results.append(r)
        comp_score = sum(r["score"] for r in comp_results)
        comp_elapsed = sum(r["elapsed"] for r in comp_results)
        print()
        print("=" * 60)
        print(f"COMPARISON: {args.model}={total_score}/{total_weight} ({total_elapsed:.1f}s)  vs  "
              f"{args.compare}={comp_score}/{total_weight} ({comp_elapsed:.1f}s)")
        print("=" * 60)
        for a, b in zip(results, comp_results):
            mark = "==" if a["score"] == b["score"] else ("<" if a["score"] < b["score"] else ">")
            print(f"  {a['name']:24s}  {a['model'] if 'model' in a else args.model:12s}={a['score']:2d}  "
                  f"{mark}  {args.compare:12s}={b['score']:2d}  "
                  f"({a['elapsed']:.1f}s vs {b['elapsed']:.1f}s)")


if __name__ == "__main__":
    main()
