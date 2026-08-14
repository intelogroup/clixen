#!/usr/bin/env python
"""
Turn mined trace failures (scripts/trace_candidates.py) into replayable eval
cases — the "generate evals from trace data" step from the LangChain talk
(mine failures -> curate -> turn into regression evals), missing piece next
to trace_candidates.py (forgememo text) and forge_principles.py (read-back).

For each recurring failure pattern, pulls the actual failing tool call (name +
args + error) from its example run and writes one replayable case. Args are
truncated to 80 chars at trace-record time (agents/local_agent_nodes.py) so
a replay is a best-effort regression check, not a byte-exact repro.

Usage:
    .venv/bin/python evals/generate_mined_evals.py [--min-count N] [--limit RUNS]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.trace_candidates import mine
from store import trace_store

_OUT_PATH = Path(__file__).parent / "mined_failure_cases.jsonl"


def _find_failing_call(run_id: str, pattern: str) -> dict | None:
    """The candidate pattern is f"{tool}: {msg}" (see trace_candidates._error_key).
    Re-derive tool name and find the matching errored entry in the example run."""
    tool = pattern.split(":", 1)[0]
    entries = trace_store.get_trace(run_id) or []
    for entry in entries:
        if entry.get("error") and entry.get("tool") == tool:
            return entry
    return None


def generate(min_count: int, limit: int) -> list[dict]:
    candidates = mine(limit, min_count)
    cases = []
    for c in candidates:
        entry = _find_failing_call(c["example_run_id"], c["pattern"])
        if entry is None:
            continue
        cases.append({
            "pattern": c["pattern"],
            "count": c["count"],
            "source_run_id": c["example_run_id"],
            "tool": entry.get("tool"),
            "args": entry.get("args", {}),
            "prior_error": entry.get("result_summary"),
        })
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    cases = generate(args.min_count, args.limit)
    with open(_OUT_PATH, "w") as f:
        for case in cases:
            f.write(json.dumps(case) + "\n")

    print(f"wrote {len(cases)} mined eval cases -> {_OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
