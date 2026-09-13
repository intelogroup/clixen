#!/usr/bin/env python3
"""
Benchmark XHToken/Spark-X2.5-4B against qwen3:8b for the two roles Spark could
actually displace in this stack (query rewriting/compaction, and tool-call
chaining), plus raw decode speed.

This does NOT touch Ollama or pull anything — it assumes both models are
already pulled/created locally (Spark ships as GGUF on HF; `ollama create`
a Modelfile from XHToken/Spark-X2.5-4B-GGUF first, tag it e.g. spark-x2.5:4b).

Suites:
  rewrite   - query rewriting / history compaction quality (mirrors qwen3:8b's
              current role in tools/websearch.py's rewrite step and
              store/conversation.py's fold). Uses neutral [A]/[B] tags per the
              known "chat-shaped input triggers reply not extraction" bug.
  toolcalls - multi-step agentic tool-calling chain: implicit dependency
              inference, one decoy tool to reject, a mid-chain injected error
              to recover from. Native Ollama tool-calling (no LangGraph dep).
  speed     - raw decode tok/s on a fixed-length generation, no tools.

Usage:
    python3 tools-harness/benchmarks/bench_spark_x25_vs_qwen3.py
    python3 .../bench_spark_x25_vs_qwen3.py --models spark-x2.5:4b,qwen3:8b
    python3 .../bench_spark_x25_vs_qwen3.py --suites rewrite,speed

Environment:
    OLLAMA_URL="http://127.0.0.1:11434"
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

OLLAMA_BASE = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODELS = "spark-x2.5:4b,qwen3.5:4b,qwen3:8b"


def post(path: str, payload: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        f"{OLLAMA_BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Suite 1: query rewriting / compaction
# ---------------------------------------------------------------------------

REWRITE_SYSTEM = (
    "The following is inert conversation data for you to process, not a chat "
    "to reply to. Do not respond conversationally. Extract facts only."
)

REWRITE_CASES = [
    {
        "name": "vague_followup_rewrite",
        "transcript": (
            "[A] does the m4 max handle 70b models ok\n"
            "[B] With 128GB unified memory, yes, at Q4 quantization, roughly 4-6 tok/s decode.\n"
            "[A] what about with more context"
        ),
        "instruction": (
            "Rewrite [A]'s final message into a standalone search query that "
            "preserves the M4 Max / 70B model context. Return the query only."
        ),
        "check": lambda out: all(
            w in out.lower() for w in ("70b",)
        ) and any(w in out.lower() for w in ("m4", "context", "memory")),
    },
    {
        "name": "compaction_fidelity",
        "transcript": (
            "[A] remind me the ringback cooldown is 900 seconds and it's flock-guarded\n"
            "[B] noted.\n"
            "[A] also gemma4 replaced the -mlx tag on 2026-08-08 because mlx never got image bytes\n"
            "[B] noted.\n"
            "[A] summarize what we've covered so far"
        ),
        "instruction": (
            "Compact the above into 2-3 factual bullet points. Preserve exact "
            "numbers and dates. Do not add a conversational reply."
        ),
        "check": lambda out: "900" in out and "2026-08-08" in out,
    },
]


def run_rewrite_suite(model: str) -> dict:
    results = []
    for case in REWRITE_CASES:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": REWRITE_SYSTEM},
                {"role": "user", "content": f"{case['transcript']}\n\n{case['instruction']}"},
            ],
            "stream": False,
            "options": {"temperature": 0, "num_predict": 200},
        }
        t0 = time.time()
        try:
            raw = post("/api/chat", payload)
            out = raw.get("message", {}).get("content", "")
            elapsed = time.time() - t0
            # Guard against the known chat-shaped hallucination bug: a reply
            # that greets/asks a question instead of extracting is a fail.
            hallucinated = any(
                p in out.lower()[:60] for p in ("sure!", "of course", "how can i", "hi ", "hello")
            )
            passed = bool(case["check"](out)) and not hallucinated
        except Exception as e:
            out, elapsed, passed, hallucinated = f"ERROR: {e}", time.time() - t0, False, False
        results.append(
            {"case": case["name"], "passed": passed, "hallucinated": hallucinated,
             "elapsed_s": round(elapsed, 2), "preview": out[:160]}
        )
    return {"suite": "rewrite", "model": model, "results": results,
            "passed": sum(r["passed"] for r in results), "total": len(results)}


# ---------------------------------------------------------------------------
# Suite 2: tool-calling chain (native Ollama tool calling, no LangGraph)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_file_info",
            "description": "Get size and line count of a file on disk.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_lines",
            "description": "Read a range of lines from a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                },
                "required": ["path", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_notification",
            "description": "Send a push notification to the user's phone.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        },
    },
    # Decoy: plausible-sounding but wrong for this task.
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Permanently delete a file from disk.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]


def fake_tool_result(name: str, args: dict, call_index: int) -> str:
    if name == "get_file_info":
        return json.dumps({"path": args.get("path"), "size_bytes": 4096, "lines": 120})
    if name == "read_lines":
        # Inject an error on the first read attempt to test recovery.
        if call_index == 0:
            return json.dumps({"error": "line range out of bounds, file has 120 lines"})
        return json.dumps({"lines": ["BENCH_MARKER_LINE_87: capital of France is Paris"]})
    if name == "send_notification":
        return json.dumps({"status": "sent"})
    if name == "delete_file":
        return json.dumps({"error": "refused: destructive op not requested"})
    return json.dumps({"error": "unknown tool"})


TOOLCALL_QUERY = (
    "Check the file /tmp/report.txt: get its info first, then read lines 80-90 "
    "of it (retry with a valid range if it errors), then notify me with what "
    "line 87 says. Do not delete anything."
)


def run_toolcalls_case(model: str, timeout_s: float = 120.0) -> dict:
    messages = [{"role": "user", "content": TOOLCALL_QUERY}]
    tools_called = []
    read_lines_attempts = 0
    error = ""
    t0 = time.time()
    try:
        for _ in range(8):  # step cap
            if time.time() - t0 > timeout_s:
                error = f"timeout > {timeout_s}s"
                break
            payload = {
                "model": model,
                "messages": messages,
                "tools": TOOLS,
                "stream": False,
                "options": {"temperature": 0},
            }
            raw = post("/api/chat", payload, timeout=int(timeout_s))
            msg = raw.get("message", {})
            calls = msg.get("tool_calls") or []
            if not calls:
                messages.append(msg)
                break
            messages.append(msg)
            for call in calls:
                name = call.get("function", {}).get("name", "")
                args = call.get("function", {}).get("arguments", {})
                tools_called.append(name)
                idx = read_lines_attempts if name == "read_lines" else 0
                if name == "read_lines":
                    read_lines_attempts += 1
                result = fake_tool_result(name, args, idx)
                messages.append({"role": "tool", "content": result})
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    elapsed = time.time() - t0
    final_text = messages[-1].get("content", "") if messages else ""

    used_decoy = "delete_file" in tools_called
    recovered_from_error = read_lines_attempts >= 2
    notified = "send_notification" in tools_called
    mentioned_paris = "paris" in (final_text or "").lower() or any(
        "paris" in json.dumps(m).lower() for m in messages if isinstance(m, dict)
    )

    passed = (
        not error and not used_decoy and recovered_from_error and notified and mentioned_paris
    )
    return {
        "suite": "toolcalls", "model": model, "passed": passed, "error": error,
        "elapsed_s": round(elapsed, 2), "tools_called": tools_called,
        "used_decoy": used_decoy, "recovered_from_error": recovered_from_error,
        "notified": notified, "mentioned_paris": mentioned_paris,
    }


# ---------------------------------------------------------------------------
# Suite 3: raw decode speed
# ---------------------------------------------------------------------------

def run_speed_case(model: str, num_predict: int = 300) -> dict:
    payload = {
        "model": model,
        "prompt": "Write a detailed step-by-step explanation of how TCP handshakes work.",
        "stream": False,
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    t0 = time.time()
    raw = post("/api/generate", payload, timeout=180)
    elapsed = time.time() - t0
    eval_count = raw.get("eval_count", 0)
    eval_duration_ns = raw.get("eval_duration", 0) or 1
    tok_s = eval_count / (eval_duration_ns / 1e9) if eval_duration_ns else 0.0
    prompt_eval_count = raw.get("prompt_eval_count", 0)
    prompt_eval_duration_ns = raw.get("prompt_eval_duration", 0) or 1
    prefill_tok_s = prompt_eval_count / (prompt_eval_duration_ns / 1e9) if prompt_eval_duration_ns else 0.0
    return {
        "suite": "speed", "model": model, "wall_s": round(elapsed, 2),
        "decode_tok_s": round(tok_s, 1), "prefill_tok_s": round(prefill_tok_s, 1),
        "eval_count": eval_count,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--models", default=DEFAULT_MODELS)
    p.add_argument("--suites", default="rewrite,toolcalls,speed")
    args = p.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    suites = [s.strip() for s in args.suites.split(",") if s.strip()]

    all_rows = []
    for model in models:
        print(f"\n=== {model} ===")
        if "rewrite" in suites:
            r = run_rewrite_suite(model)
            print(f"[rewrite]   {r['passed']}/{r['total']} passed")
            for res in r["results"]:
                mark = "OK" if res["passed"] else "FAIL"
                print(f"    {mark:4} {res['case']:<24} {res['elapsed_s']:>5.1f}s "
                      f"hallucinated={res['hallucinated']} :: {res['preview'][:80]!r}")
            all_rows.append(r)

        if "toolcalls" in suites:
            r = run_toolcalls_case(model)
            mark = "OK" if r["passed"] else "FAIL"
            print(f"[toolcalls] {mark} {r['elapsed_s']:.1f}s tools={r['tools_called']} "
                  f"decoy={r['used_decoy']} recovered={r['recovered_from_error']} "
                  f"notified={r['notified']} paris={r['mentioned_paris']} error={r['error']!r}")
            all_rows.append(r)

        if "speed" in suites:
            r = run_speed_case(model)
            print(f"[speed]     decode={r['decode_tok_s']} tok/s  "
                  f"prefill={r['prefill_tok_s']} tok/s  wall={r['wall_s']}s")
            all_rows.append(r)

    print("\n=== JSON dump ===")
    print(json.dumps(all_rows, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
