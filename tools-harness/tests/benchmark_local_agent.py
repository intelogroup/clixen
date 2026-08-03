"""
Benchmark: local-agent filesystem tools — mistral-nemo vs gemma4.

Fair comparison rules:
- Each query gets its own fresh chat_id (no history bleed between tasks).
- Setup/teardown ensures the filesystem state is correct before each task.
- Tool-call detection verifies the model actually called a tool vs hallucinating.
- Models are tested sequentially; timing includes Ollama round-trip + tool execution.

Usage:
    python tests/benchmark_local_agent.py              # both models
    python tests/benchmark_local_agent.py --model nemo
    python tests/benchmark_local_agent.py --model gemma4
"""

import argparse
import io
import shutil
import sys
import time
import uuid
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from harness import run

# ---------------------------------------------------------------------------
# Task definitions
# Each task has: query, expected_tool, optional setup() and cleanup() callables.
# ---------------------------------------------------------------------------

_TMP = Path("/tmp/la_bench")

TASKS = [
    {
        "name": "find_largest",
        "query": "what is the biggest file in ~/Developer",
        "expected_tool": "find_largest",
        "setup": None,
        "cleanup": None,
    },
    {
        "name": "list_directory",
        "query": "list files in ~/Desktop",
        "expected_tool": "list_directory",
        "setup": None,
        "cleanup": None,
    },
    {
        "name": "create_directory",
        "query": f"create a folder called la_bench_new in {_TMP}",
        "expected_tool": "create_directory",
        "setup": lambda: _TMP.mkdir(exist_ok=True),
        "cleanup": lambda: shutil.rmtree(_TMP / "la_bench_new", ignore_errors=True),
    },
    {
        "name": "rename_file",
        "query": f"rename {_TMP}/la_bench_src to {_TMP}/la_bench_dst",
        "expected_tool": "rename_file",
        "setup": lambda: (_TMP.mkdir(exist_ok=True), (_TMP / "la_bench_src").mkdir(exist_ok=True)),
        "cleanup": lambda: shutil.rmtree(_TMP / "la_bench_dst", ignore_errors=True),
    },
    {
        "name": "delete_file",
        "query": f"delete folder {_TMP}/la_bench_del",
        "expected_tool": "delete_file",
        "setup": lambda: (_TMP.mkdir(exist_ok=True), (_TMP / "la_bench_del").mkdir(exist_ok=True)),
        "cleanup": None,
    },
    {
        "name": "copy_file",
        "query": f"copy folder {_TMP}/la_bench_copy_src to {_TMP}/la_bench_copy_dst",
        "expected_tool": "copy_file",
        "setup": lambda: (
            _TMP.mkdir(exist_ok=True),
            (_TMP / "la_bench_copy_src").mkdir(exist_ok=True),
            (_TMP / "la_bench_copy_src" / "file.txt").write_text("hello"),
        ),
        "cleanup": lambda: (
            shutil.rmtree(_TMP / "la_bench_copy_src", ignore_errors=True),
            shutil.rmtree(_TMP / "la_bench_copy_dst", ignore_errors=True),
        ),
    },
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_task(task: dict, model: str) -> dict:
    """Run a single task and return a result dict."""
    if task["setup"]:
        task["setup"]()

    chat_id = f"ide_bench_{uuid.uuid4().hex[:8]}"  # fresh state, ide_ prefix bypasses tool-stripping

    # Capture stdout to detect [tool] lines printed by harness
    buf = io.StringIO()
    t0 = time.time()
    try:
        with redirect_stdout(buf):
            reply, _model, intent = run(task["query"], model=model, chat_id=chat_id)
        elapsed = time.time() - t0
        captured = buf.getvalue()
        tool_called = f"[tool] {task['expected_tool']}" in captured
        error = None
    except Exception as exc:
        elapsed = time.time() - t0
        reply = str(exc)
        tool_called = False
        error = str(exc)
        intent = "?"

    if task["cleanup"]:
        task["cleanup"]()

    return {
        "task": task["name"],
        "model": model,
        "intent": intent,
        "elapsed": elapsed,
        "tool_called": tool_called,
        "reply": reply.strip(),
        "error": error,
    }


def _print_results(results: list[dict], model: str):
    total = len(results)
    passed = sum(1 for r in results if r["tool_called"] and not r["error"])
    total_time = sum(r["elapsed"] for r in results)

    print(f"\n{'='*60}")
    print(f"  {model.upper()}  —  {passed}/{total} tools called  |  {total_time:.1f}s total")
    print(f"{'='*60}")

    for r in results:
        status = "PASS" if r["tool_called"] and not r["error"] else "FAIL"
        flag = "[tool called]" if r["tool_called"] else "[NO TOOL]"
        print(f"  [{status}] {r['task']:20s}  {r['elapsed']:5.1f}s  {flag}")
        # Show first 120 chars of reply
        short = r["reply"][:120].replace("\n", " ")
        print(f"         reply: {short}")
        if r["error"]:
            print(f"         error: {r['error']}")
    print()


def run_benchmark(models: list[str]):
    print("\nlocal-agent benchmark — filesystem tool calling")
    print(f"Tasks: {len(TASKS)}  |  Models: {', '.join(models)}")
    print("Each query uses a fresh chat_id (no history bleed).\n")

    all_results = {}
    for model in models:
        print(f"--- Running {model} ---")
        results = []
        for task in TASKS:
            print(f"  {task['name']}... ", end="", flush=True)
            r = _run_task(task, model)
            flag = "OK" if r["tool_called"] else "NO TOOL"
            print(f"{r['elapsed']:.1f}s [{flag}]")
            results.append(r)
        all_results[model] = results

    for model, results in all_results.items():
        _print_results(results, model)

    if len(models) == 2:
        _print_comparison(all_results[models[0]], all_results[models[1]], models[0], models[1])


def _print_comparison(r1: list, r2: list, m1: str, m2: str):
    print(f"{'='*60}")
    print(f"  SIDE-BY-SIDE  {m1} vs {m2}")
    print(f"{'='*60}")
    print(f"  {'Task':<20}  {m1:>14}  {m2:>14}")
    print(f"  {'-'*20}  {'-'*14}  {'-'*14}")
    for a, b in zip(r1, r2):
        a_tag = f"{a['elapsed']:.1f}s {'✓' if a['tool_called'] else '✗'}"
        b_tag = f"{b['elapsed']:.1f}s {'✓' if b['tool_called'] else '✗'}"
        print(f"  {a['task']:<20}  {a_tag:>14}  {b_tag:>14}")
    t1 = sum(r["elapsed"] for r in r1)
    t2 = sum(r["elapsed"] for r in r2)
    p1 = sum(1 for r in r1 if r["tool_called"])
    p2 = sum(1 for r in r2 if r["tool_called"])
    print(f"  {'-'*20}  {'-'*14}  {'-'*14}")
    print(f"  {'TOTAL':<20}  {t1:>12.1f}s  {t2:>12.1f}s")
    print(f"  {'TOOLS CALLED':<20}  {p1:>12}/{len(r1)}  {p2:>12}/{len(r2)}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        choices=["nemo", "gemma4", "both"],
        default="both",
        help="Which model to test (default: both)",
    )
    args = parser.parse_args()

    model_map = {"nemo": ["mistral-nemo"], "gemma4": ["gemma4"], "both": ["mistral-nemo", "gemma4"]}
    run_benchmark(model_map[args.model])
