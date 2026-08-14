"""
Tier 2 (live) — replays mined failure cases (evals/mined_failure_cases.jsonl,
built by generate_mined_evals.py) against the real tool executor. A case that
now succeeds is a regression fixed; a case that still errors the same way is
still broken. Run after any tool/executor change to see what moved.

Skips tools with side effects (messaging, calls, email, destructive fs/shell) —
replaying those isn't a safe regression check, it's re-sending the original
message/call. Extend _UNSAFE_TOOLS if a new side-effecting tool starts showing
up in mined cases.

Usage: ./.venv/bin/python -m pytest evals/test_mined_failures.py -m live
"""
import json
from pathlib import Path

import pytest

_CASES_PATH = Path(__file__).parent / "mined_failure_cases.jsonl"

_UNSAFE_TOOLS = {
    "send_message", "send_telegram_message", "send_email", "call_my_phone",
    "send_imessage", "delete_file", "shell_exec", "run_shell_command",
}


def _load_cases() -> list[dict]:
    if not _CASES_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in _CASES_PATH.read_text().splitlines()
        if line.strip()
    ]


pytestmark = pytest.mark.live


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["pattern"][:60])
def test_mined_failure_still_reproduces(case):
    if case["tool"] in _UNSAFE_TOOLS:
        pytest.skip(f"{case['tool']} has side effects, not safe to replay")

    from tools.registry import execute_tool

    result = execute_tool(case["tool"], case.get("args", {}))
    still_failing = isinstance(result, str) and result.startswith("[error]")

    if still_failing:
        pytest.fail(
            f"still reproduces (seen {case['count']}x): {result[:200]}"
        )
