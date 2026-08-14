#!/usr/bin/env python
"""
Mine trace_store for recurring failure patterns and print optimization
candidates as JSON — one line per candidate, ready to hand to forgememo
steering or eyeball manually.

Usage:
    .venv/bin/python scripts/trace_candidates.py [--min-count N] [--limit RUNS]
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from store import trace_store

_PUSHED_PATH = Path(__file__).parent.parent / "data" / "trace_candidates_pushed.json"


def _pattern_hash(pattern: str) -> str:
    return hashlib.sha256(pattern.encode()).hexdigest()[:16]


def _load_pushed() -> dict:
    if not _PUSHED_PATH.exists():
        return {}
    return json.loads(_PUSHED_PATH.read_text())


def _save_pushed(pushed: dict) -> None:
    _PUSHED_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PUSHED_PATH.write_text(json.dumps(pushed, indent=2))


def _error_key(entry: dict) -> str:
    tool = entry.get("tool") or "?"
    # error is a bool flag; the actual message lives in result_summary
    # (prefixed "[error] ..." — see harness.py tool loop)
    msg = (entry.get("result_summary") or "").strip()
    if msg.startswith("[error]"):
        msg = msg[len("[error]"):].strip()
    msg = msg.splitlines()[0][:80] if msg else "(no message)"
    return f"{tool}: {msg}"


def mine(limit: int, min_count: int, use_judge: bool = False) -> list[dict]:
    runs = trace_store.query_traces(has_error=True, limit=limit)

    error_counts: Counter = Counter()
    example_run = {}
    degraded_runs = 0

    for run in runs:
        run_id = run["run_id"]
        entries = run["entries"]
        summary = trace_store.summarize_run(run_id)
        if summary["status"] == "degraded":
            degraded_runs += 1
        for entry in entries:
            if not entry.get("error"):
                continue
            if use_judge:
                from tools.trace_judge import judge_pattern
                msg = (entry.get("result_summary") or "").strip()
                if msg.startswith("[error]"):
                    msg = msg[len("[error]"):].strip()
                key = judge_pattern(entry.get("tool") or "?", msg)
            else:
                key = _error_key(entry)
            error_counts[key] += 1
            example_run.setdefault(key, run_id)

    candidates = []
    for key, count in error_counts.most_common():
        if count < min_count:
            continue
        candidates.append({
            "pattern": key,
            "count": count,
            "example_run_id": example_run[key],
        })

    candidates.sort(key=lambda c: -c["count"])
    return candidates


def push_to_forge(candidates: list[dict]) -> None:
    """Feed each candidate into forgememo as a failure memory — same `forge save`
    path scan/distill already use, so these surface through get_active_failures /
    get_principles / steering like any other learned failure.

    Dedup: a pattern already pushed at count N is skipped on rerun unless the
    count has grown since (still happening, worth re-surfacing the escalation).
    """
    forge_bin = shutil.which("forge")
    if not forge_bin:
        print("forge binary not found on PATH — skipping push", file=sys.stderr)
        return

    pushed = _load_pushed()
    for c in candidates:
        h = _pattern_hash(c["pattern"])
        prev_count = pushed.get(h, {}).get("count", 0)
        if c["count"] <= prev_count:
            print(f"skip (already pushed, count unchanged): {c['pattern']}", file=sys.stderr)
            continue

        content = f"Recurring tool failure: {c['pattern']} (seen {c['count']}x, e.g. run_id={c['example_run_id']})"
        subprocess.run(
            [forge_bin, "save", "--type", "failure", "--content", content],
            check=True,
        )
        pushed[h] = {"pattern": c["pattern"], "count": c["count"]}
        print(f"pushed: {content}", file=sys.stderr)

    _save_pushed(pushed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-count", type=int, default=2, help="drop patterns seen fewer than N times")
    ap.add_argument("--limit", type=int, default=200, help="max runs to scan")
    ap.add_argument("--push", action="store_true", help="save each candidate into forgememo (forge save --type failure)")
    ap.add_argument("--judge", action="store_true", help="cluster by local-model-judged root-cause category instead of exact-string prefix")
    args = ap.parse_args()

    candidates = mine(args.limit, args.min_count, use_judge=args.judge)
    print(json.dumps(candidates, indent=2))
    print(f"\n{len(candidates)} recurring failure patterns", file=sys.stderr)

    if args.push:
        push_to_forge(candidates)


if __name__ == "__main__":
    main()
