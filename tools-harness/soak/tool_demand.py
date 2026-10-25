#!/usr/bin/env python3
"""Should this capability be a tool? Answer with calls, not opinion.

The rule (docs/adr/0005): a site-specific capability becomes a tool at >= 20 calls,
when it also has structured data and a stable API. Below that it stays a recipe or
a skill, because every tool costs 450-900 chars of schema on EVERY round and the
orb prompt lives at ~3 200 chars total.

    python soak/tool_demand.py                     # scorecard from the real log
    python soak/tool_demand.py --threshold 20 --json
    python soak/tool_demand.py --log path/to/core_stdout.log

Deliberately prints NOT ENOUGH DATA instead of ranking on three calls.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOG_DEFAULT = ROOT / "core_stdout.log"
TOOL_LINE = re.compile(r"\[tool\] ([a-z_][a-z0-9_]*)\(([^\n]*)$")

# Names that mean "this capability belongs to one site". Kept explicit rather than
# guessed from prefixes: a heuristic that silently mislabels a generic tool as
# site-specific would send us building adapters for things act/read already do.
SITE_TOKENS = (
    "github", "gitlab", "jira", "linear", "notion", "slack", "reddit", "youtube",
    "twitter", "x_", "linkedin", "arxiv", "pubmed", "gdelt", "hacker", "stack",
    "whatsapp", "imessage", "telegram", "doordash", "uber", "spotify", "gmail",
    "calendar", "drive", "sheets", "docs", "kaggle", "producthunt", "shopify",
)

# Capabilities we already shipped as adapters: the gate should say "keep", not
# "promote again".
ADAPTERS = {
    "wikipedia_references", "list_emails", "read_email", "get_latest_email",
    "list_calendar_events", "create_calendar_event", "list_tasks", "complete_task",
    "list_reminders", "system_status", "list_mac_calendar_events", "imessage_search",
    "create_reminder",
}

# Transports, not sites: sending a Telegram message is a channel, and a "promote
# this to an adapter" verdict for it would be nonsense.
CHANNELS = {
    "send_telegram", "send_email", "imessage_send", "send_slack_message",
    "send_whatsapp", "make_phone_call", "ask_send_message",
}


def _classify(name: str) -> str:
    if name in ADAPTERS:
        return "adapter"
    if name in CHANNELS:
        return "channel"
    # ask_*_agent is a subagent: its own tool loop, its own round. Worth flagging
    # separately because the fix is usually "use the direct adapter", not "build one".
    if name.startswith("ask_") and name.endswith("_agent"):
        return "subagent"
    low = name.lower()
    if any(token in low for token in SITE_TOKENS):
        return "site-specific"
    return "generic"


def _schema_chars() -> dict[str, int]:
    try:
        from tools.registry import ALL_TOOLS

        return {
            t["function"]["name"]: len(json.dumps(t))
            for t in ALL_TOOLS
        }
    except Exception:
        return {}


def _counts(log_path: pathlib.Path) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    if not log_path.exists():
        return counts
    with log_path.open(errors="ignore") as fh:
        for line in fh:
            m = TOOL_LINE.search(line)
            if m:
                counts[m.group(1)] += 1
    return counts


def scorecard(counts, chars: dict[str, int], threshold: int) -> list[dict]:
    rows = []
    # Any mapping works: tests pass plain dicts, the CLI passes a Counter.
    for name, calls in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        kind = _classify(name)
        if kind == "adapter":
            verdict = "keep (already an adapter)"
        elif kind == "channel":
            verdict = "keep (transport, not a site)"
        elif kind == "subagent":
            verdict = "prefer the direct adapter if one exists (a subagent costs a round)"
        elif kind == "site-specific":
            if calls >= threshold:
                verdict = f"PROMOTE: >= {threshold} calls — build a data adapter"
            else:
                verdict = f"hold: {threshold - calls} more calls to reach the gate"
        else:
            verdict = "generic primitive"
        rows.append({
            "tool": name,
            "calls": calls,
            "class": kind,
            "schema_chars": chars.get(name, 0),
            "verdict": verdict,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(LOG_DEFAULT))
    ap.add_argument("--threshold", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    log_path = pathlib.Path(args.log)
    counts = _counts(log_path)
    if not counts:
        print(f"NOT ENOUGH DATA: no '[tool] name(' lines in {log_path}", file=sys.stderr)
        return 2

    rows = scorecard(counts, _schema_chars(), args.threshold)
    if args.json:
        print(json.dumps({"log": str(log_path), "threshold": args.threshold,
                          "total_calls": sum(counts.values()), "rows": rows}, indent=2))
        return 0

    print(f"{sum(counts.values())} tool calls in {log_path.name}; gate = {args.threshold} calls\n")
    print(f"{'tool':30s} {'calls':>6s} {'schema':>7s}  {'class':14s} verdict")
    for row in rows[: args.top]:
        print(f"{row['tool']:30s} {row['calls']:6d} {row['schema_chars']:7d}  "
              f"{row['class']:14s} {row['verdict']}")

    site = [r for r in rows if r["class"] == "site-specific"]
    print(f"\nsite-specific tools in use: {len(site)} "
          f"({100 * sum(r['calls'] for r in site) / max(1, sum(counts.values())):.1f}% of calls)")
    promote = [r for r in site if r["calls"] >= args.threshold]
    print("promotions due: " + (", ".join(r["tool"] for r in promote) if promote else "none"))
    never_used = sorted(name for name in _schema_chars() if counts.get(name, 0) == 0)
    print(f"registered but never called: {len(never_used)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
