"""
Cross-model tool-calling benchmark for the local-agent ReAct graph.

Runs the same set of realistic queries through multiple models and reports:
- tool selection accuracy (did it pick a valid tool?)
- final-answer validator pass rate
- latency per query
- total ReAct steps (tool calls)

Models compared (default): gemma4 (local), deepseek/deepseek-v4-flash (DeepSeek direct API),
openrouter/anthropic/claude-haiku-4.5 (cloud, 2026-07 revamp — see clients/cloud_client.py).
Override via: --models gemma4,qwen3:4b

Usage:
    python -m tests.benchmark_models_tool_calling
    python -m tests.benchmark_models_tool_calling --models qwen3:4b,qwen3.5:4b
    python -m tests.benchmark_models_tool_calling --cases shell,fs
    python -m tests.benchmark_models_tool_calling --repeats 5   # reliability score:
        runs each case N times per model, reports success_rate = passes/N —
        a single-pass run won't catch flaky multi-step drift.

Each case is ~12-30s warm. Default 5 cases × 3 models = ~5-10 min (x --repeats).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.local_agent_graph import run_local_agent_streaming  # noqa: E402
from clients.cloud_client import is_cloud_model, get_usage_totals, reset_usage_totals  # noqa: E402

C = {
    "g": "\033[32m", "r": "\033[31m", "y": "\033[33m",
    "c": "\033[36m", "m": "\033[35m", "b": "\033[1m",
    "d": "\033[2m", "x": "\033[0m",
}


# ----- Fixtures (same as verify_local_agent_e2e) -----

FIXTURE_DIR = Path("/tmp/g4l_bench_fixtures")
FIXTURE_DIR.mkdir(exist_ok=True)
FIXTURE_TXT = FIXTURE_DIR / "bench_read_me.txt"
FIXTURE_TXT.write_text(
    "BENCH_FIXTURE_MARKER\n"
    "The capital of France is Paris.\n"
    "The square root of 144 is 12.\n"
)


def _contains(needle: str) -> Callable[[str], bool]:
    return lambda r: needle.lower() in r.lower()


# ----- Cases (lightweight; no audio/pdf to keep timing tight) -----

CASES = [
    {
        "group": "fs",
        "name": "list /tmp",
        "query": "list the files in the /tmp directory",
        "expected_tools": ["list_directory", "bash_exec"],
        "validator": lambda r: bool(r and len(r) > 20),
    },
    {
        "group": "fs",
        "name": "read fixture",
        "query": f"read the file at {FIXTURE_TXT} and tell me what's in it",
        "expected_tools": ["read_file", "read_document"],
        "validator": _contains("paris"),
    },
    {
        "group": "fs",
        "name": "find file",
        "query": "find files matching AUDIO-2026 in my Downloads folder",
        "expected_tools": ["find_files"],
        "validator": lambda r: bool(r and ("audio" in r.lower() or "downloads" in r.lower())),
    },
    {
        "group": "fs",
        "name": "write file",
        "query": f"create a file at {FIXTURE_DIR}/wrote.txt with content 'hello'",
        "expected_tools": ["write_file", "create_directory"],
        "validator": lambda r: (FIXTURE_DIR / "wrote.txt").exists(),
    },
    {
        "group": "shell",
        "name": "bash echo",
        "query": "run a shell command that prints 'BENCH_MARKER_xyz'",
        "expected_tools": ["bash_exec"],
        "validator": _contains("bench_marker_xyz"),
    },
]


# ----- Runner -----

def run_case(model: str, case: dict, timeout_s: float = 240.0) -> dict:
    """Run one case against one model. Returns dict with metrics."""
    tools_called: list[str] = []
    final_response = ""
    error = ""
    t0 = time.time()

    try:
        for evt, payload in run_local_agent_streaming(query=case["query"], model=model):
            if time.time() - t0 > timeout_s:
                error = f"timeout > {timeout_s}s"
                break
            if evt == "tool_call":
                tools_called.append(payload.get("name", ""))
            elif evt == "final":
                final_response = payload or ""
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    elapsed = time.time() - t0

    tool_ok = bool(case["expected_tools"]) and any(
        t in tools_called for t in case["expected_tools"]
    )
    validator_ok = False
    try:
        validator_ok = bool(case["validator"](final_response))
    except Exception:
        validator_ok = False

    return {
        "model": model,
        "case": case["name"],
        "tools_called": tools_called,
        "final_response": final_response,
        "elapsed_s": elapsed,
        "tool_ok": tool_ok,
        "validator_ok": validator_ok,
        "passed": tool_ok and validator_ok and not error,
        "error": error,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--models",
        default="gemma4,deepseek/deepseek-v4-flash,openrouter/anthropic/claude-haiku-4.5",
    )
    p.add_argument("--cases", default="", help="Comma-separated group/name filters")
    p.add_argument("--timeout", type=float, default=240.0)
    p.add_argument(
        "--repeats", type=int, default=1,
        help="Run each case N times per model and report success_rate = passes/N "
        "(catches flaky multi-step drift a single pass won't).",
    )
    args = p.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    case_filter = [s.strip() for s in args.cases.split(",") if s.strip()]
    cases = CASES
    if case_filter:
        cases = [c for c in CASES if any(f in c["group"] or f in c["name"] for f in case_filter)]

    print(f"{C['b']}{C['c']}=== Cross-Model Tool-Calling Benchmark ==={C['x']}")
    print(f"{C['d']}models : {models}{C['x']}")
    print(f"{C['d']}cases  : {[c['name'] for c in cases]}{C['x']}")
    print(f"{C['d']}repeats: {args.repeats} per case{C['x']}")
    print(f"{C['d']}timeout: {args.timeout}s per case{C['x']}\n")

    results: list[dict] = []
    usage_by_model: dict[str, dict] = {}
    for mi, model in enumerate(models, 1):
        print(f"{C['m']}{C['b']}── Model [{mi}/{len(models)}]: {model} ──{C['x']}")
        if is_cloud_model(model):
            reset_usage_totals()
        for ci, case in enumerate(cases, 1):
            passes = 0
            last_r = None
            for rep in range(args.repeats):
                rep_label = f" rep {rep + 1}/{args.repeats}" if args.repeats > 1 else ""
                label = f"  [{ci}/{len(cases)}] {case['group']}/{case['name']}{rep_label}"
                print(f"{C['y']}{label}{C['x']}", end=" ", flush=True)
                r = run_case(model, case, timeout_s=args.timeout)
                results.append(r)
                last_r = r
                if r["passed"]:
                    passes += 1
                mark = f"{C['g']}✓{C['x']}" if r["passed"] else f"{C['r']}✗{C['x']}"
                tools_str = "+".join(r["tools_called"]) or "-"
                print(f"{mark} {r['elapsed_s']:5.1f}s tools={tools_str}")
                if not r["passed"]:
                    if r["error"]:
                        print(f"      {C['r']}error:{C['x']} {r['error'][:120]}")
                    else:
                        reason = []
                        if not r["tool_ok"]:
                            reason.append(f"want {case['expected_tools']}, got {r['tools_called']}")
                        if not r["validator_ok"]:
                            reason.append(f"validator-fail (resp[:80]={r['final_response'][:80]!r})")
                        print(f"      {C['r']}reason:{C['x']} {'; '.join(reason)}")
            if args.repeats > 1:
                rate = passes / args.repeats
                color = C["g"] if rate == 1.0 else (C["y"] if rate > 0 else C["r"])
                print(f"      {color}success_rate: {passes}/{args.repeats} ({rate:.0%}){C['x']}")
        if is_cloud_model(model):
            usage_by_model[model] = get_usage_totals()
        print()

    # ----- Summary table -----
    print(f"{C['b']}=== Summary ==={C['x']}")
    by_model: dict[str, list[dict]] = {}
    for r in results:
        by_model.setdefault(r["model"], []).append(r)

    header = f"  {'model':<16} {'pass':>5} {'tool✓':>6} {'val✓':>5} {'tot_s':>7} {'avg_s':>6}"
    print(header)
    print(f"  {'-' * 50}")
    for model in models:
        rs = by_model.get(model, [])
        if not rs:
            continue
        passed = sum(1 for r in rs if r["passed"])
        tool_ok = sum(1 for r in rs if r["tool_ok"])
        val_ok = sum(1 for r in rs if r["validator_ok"])
        total_s = sum(r["elapsed_s"] for r in rs)
        avg_s = total_s / len(rs)
        n = len(rs)
        color = C["g"] if passed == n else (C["y"] if passed > 0 else C["r"])
        print(
            f"  {color}{model:<16}{C['x']} "
            f"{passed}/{n:<3} "
            f"{tool_ok}/{n:<4} "
            f"{val_ok}/{n:<3} "
            f"{total_s:>6.1f}s "
            f"{avg_s:>5.1f}s"
        )

    # Per-case breakdown — single-pass only; with --repeats > 1 use the
    # success_rate lines printed live above instead (this table shows one rep).
    if args.repeats == 1:
        print(f"\n{C['b']}=== Per-case (✓ pass / ✗ fail) ==={C['x']}")
        case_names = [c["name"] for c in cases]
        print(f"  {'model':<16} " + " ".join(f"{n[:10]:>11}" for n in case_names))
        for model in models:
            rs = by_model.get(model, [])
            if not rs:
                continue
            marks = []
            for cn in case_names:
                r = next((x for x in rs if x["case"] == cn), None)
                if r is None:
                    marks.append(f"{'-':>11}")
                elif r["passed"]:
                    marks.append(f"{C['g']}{'✓':>11}{C['x']}")
                else:
                    marks.append(f"{C['r']}{'✗':>11}{C['x']}")
            print(f"  {model:<16} " + " ".join(marks))

    # Token usage — cloud models only; check openrouter.ai/models for current $/token pricing.
    if usage_by_model:
        print(f"\n{C['b']}=== Cloud token usage ==={C['x']}")
        for model, per_real_model in usage_by_model.items():
            for real_model, u in per_real_model.items():
                print(
                    f"  {model} ({real_model}): "
                    f"{u['calls']} calls, {u['prompt_tokens']} prompt + "
                    f"{u['completion_tokens']} completion tokens"
                )

    # Failures sentinel
    total_failed = sum(1 for r in results if not r["passed"])
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
