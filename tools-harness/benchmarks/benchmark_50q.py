"""
Benchmark: 10 representative questions x 2 paths (auto-routing vs web_search=1) = 20 runs.
Runs sequentially, saves after each result, prints a report at the end.

Usage:
    python benchmark_50q.py
    python benchmark_50q.py --resume   # skip already-completed questions
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:9234"
STREAM_URL = f"{BASE_URL}/chat/stream"
RESULTS_FILE = Path(__file__).parent / "benchmark_50q_results.json"
TIMEOUT_S = 120  # per request

# ── 10 representative questions ───────────────────────────────────────────────
QUESTIONS = [
    # Sports — tests hallucination fix (scores/dates must be grounded)
    "What was the score in the latest Barcelona La Liga match?",
    "Who won the most recent Real Madrid game and what was the score?",

    # Temporal / live — must fetch, not recall
    "What is the current price of Bitcoin?",
    "What is the latest news about OpenAI?",

    # Static fact — correct both paths, good baseline
    "When did World War 2 end?",

    # Science — should be identical both paths
    "What is the speed of light in meters per second?",

    # Math — deterministic, no search needed
    "What is 17 multiplied by 23?",

    # Code — no search needed, tests routing
    "What is the difference between == and === in JavaScript?",

    # Ambiguous / opinion — how each path handles no ground truth
    "Who is the best footballer in the world right now?",

    # Multi-hop / explanation — tests synthesis quality
    "What caused the 2008 financial crisis?",
]

assert len(QUESTIONS) == 10, f"Expected 10 questions, got {len(QUESTIONS)}"


def stream_chat(message: str, web_search: bool, chat_id: str) -> dict:
    """Call GET /chat/stream and collect the full response."""
    params = {
        "message": message,
        "chat_id": chat_id,
        "web_search": "1" if web_search else "0",
    }
    tokens: list[str] = []
    model = ""
    intent = ""
    error = ""
    t0 = time.time()

    try:
        with requests.get(STREAM_URL, params=params, stream=True, timeout=TIMEOUT_S) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if "token" in event:
                    tokens.append(event["token"])
                if event.get("done"):
                    model = event.get("model", "")
                    intent = event.get("intent", "")
                    error = event.get("error", "")
                    break
                if "error" in event and not event.get("done"):
                    error = event["error"]
    except requests.exceptions.Timeout:
        error = f"timeout after {TIMEOUT_S}s"
    except Exception as exc:
        error = str(exc)

    elapsed = round(time.time() - t0, 1)
    answer = "".join(tokens).strip()
    return {
        "answer": answer,
        "model": model,
        "intent": intent,
        "error": error,
        "elapsed_s": elapsed,
    }


def load_results() -> list[dict]:
    if RESULTS_FILE.exists():
        return json.loads(RESULTS_FILE.read_text())
    return []


def save_results(results: list[dict]) -> None:
    RESULTS_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False))


def run(resume: bool = False) -> list[dict]:
    results = load_results() if resume else []
    done_keys = {(r["question_idx"], r["path"]) for r in results}

    total = len(QUESTIONS) * 2
    done = len(done_keys)

    for path_label, web_search in [("auto", False), ("web_search", True)]:
        for idx, question in enumerate(QUESTIONS):
            key = (idx, path_label)
            if key in done_keys:
                continue

            done += 1
            chat_id = f"bench_{uuid.uuid4().hex[:8]}"
            print(f"[{done}/{total}] [{path_label}] Q{idx+1:02d}: {question[:70]}", end="  ", flush=True)

            result = stream_chat(question, web_search=web_search, chat_id=chat_id)
            record = {
                "question_idx": idx,
                "question": question,
                "path": path_label,
                **result,
            }
            results.append(record)
            save_results(results)

            status = "ERR" if result["error"] else "ok"
            answer_preview = (result["answer"] or result["error"])[:80].replace("\n", " ")
            print(f"[{status} {result['elapsed_s']}s | {result['model']} | {result['intent']}]")
            print(f"         → {answer_preview}")

    return results


def report(results: list[dict]) -> None:
    by_q: dict[int, dict] = {}
    for r in results:
        by_q.setdefault(r["question_idx"], {})[r["path"]] = r

    print("\n" + "=" * 100)
    print("BENCHMARK REPORT — 50 Questions x 2 Paths")
    print("=" * 100)

    errors_auto = sum(1 for r in results if r["path"] == "auto" and r["error"])
    errors_web  = sum(1 for r in results if r["path"] == "web_search" and r["error"])
    avg_auto    = _avg([r["elapsed_s"] for r in results if r["path"] == "auto" and not r["error"]])
    avg_web     = _avg([r["elapsed_s"] for r in results if r["path"] == "web_search" and not r["error"]])

    print(f"\nSummary:")
    print(f"  auto       — errors: {errors_auto}/50  avg latency: {avg_auto:.1f}s")
    print(f"  web_search — errors: {errors_web}/50  avg latency: {avg_web:.1f}s")

    # Intent distribution
    intents_auto = {}
    intents_web  = {}
    models_auto  = {}
    models_web   = {}
    for r in results:
        if r["path"] == "auto":
            intents_auto[r["intent"]] = intents_auto.get(r["intent"], 0) + 1
            models_auto[r["model"]]   = models_auto.get(r["model"], 0) + 1
        else:
            intents_web[r["intent"]] = intents_web.get(r["intent"], 0) + 1
            models_web[r["model"]]   = models_web.get(r["model"], 0) + 1

    print(f"\nIntent distribution (auto):  {dict(sorted(intents_auto.items(), key=lambda x: -x[1]))}")
    print(f"Intent distribution (web):   {dict(sorted(intents_web.items(), key=lambda x: -x[1]))}")
    print(f"\nModel usage (auto):  {dict(sorted(models_auto.items(), key=lambda x: -x[1]))}")
    print(f"Model usage (web):   {dict(sorted(models_web.items(), key=lambda x: -x[1]))}")

    # Per-question comparison
    print(f"\n{'─'*100}")
    print(f"{'Q#':<4} {'Question (truncated)':<52} {'AUTO':<20} {'WEB_SEARCH':<20} {'DIFF?'}")
    print(f"{'─'*100}")

    differing = []
    empty_auto = []
    empty_web  = []

    for idx in sorted(by_q):
        q = QUESTIONS[idx]
        auto = by_q[idx].get("auto", {})
        web  = by_q[idx].get("web_search", {})
        a_ans = (auto.get("answer") or auto.get("error") or "")[:60].replace("\n", " ")
        w_ans = (web.get("answer")  or web.get("error")  or "")[:60].replace("\n", " ")

        differ = (
            auto.get("answer", "").strip().lower()[:80] !=
            web.get("answer", "").strip().lower()[:80]
        )
        diff_marker = "DIFF" if differ else ""
        print(f"{idx+1:<4} {q[:52]:<52} {a_ans:<20} {w_ans:<20} {diff_marker}")

        if differ:
            differing.append(idx)
        if not auto.get("answer") and not auto.get("error"):
            empty_auto.append(idx)
        if not web.get("answer") and not web.get("error"):
            empty_web.append(idx)

    # Detailed findings for divergent answers
    print(f"\n{'─'*100}")
    print(f"DIVERGING ANSWERS ({len(differing)} questions where auto != web_search):")
    print(f"{'─'*100}")
    for idx in differing[:20]:  # cap at 20 for readability
        auto = by_q[idx].get("auto", {})
        web  = by_q[idx].get("web_search", {})
        print(f"\nQ{idx+1}: {QUESTIONS[idx]}")
        print(f"  AUTO [{auto.get('model','')} | {auto.get('intent','')} | {auto.get('elapsed_s','')}s]:")
        print(f"    {(auto.get('answer') or auto.get('error') or '')[:200].replace(chr(10),' ')}")
        print(f"  WEB  [{web.get('model','')} | {web.get('intent','')} | {web.get('elapsed_s','')}s]:")
        print(f"    {(web.get('answer') or web.get('error') or '')[:200].replace(chr(10),' ')}")

    if empty_auto:
        print(f"\nEmpty auto answers: Q{[i+1 for i in empty_auto]}")
    if empty_web:
        print(f"Empty web answers:  Q{[i+1 for i in empty_web]}")

    print(f"\nFull results saved to: {RESULTS_FILE}")


def _avg(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true", help="Skip already-completed questions")
    parser.add_argument("--report-only", action="store_true", help="Print report from existing results")
    args = parser.parse_args()

    if args.report_only:
        results = load_results()
        if not results:
            print("No results file found. Run without --report-only first.")
            sys.exit(1)
    else:
        results = run(resume=args.resume)

    report(results)
