"""
End-to-end tests for the local-agent ReAct graph.

Verifies the *agent* (LLM + tool selection + ReAct loop), not just the tools.
Each test case sends a realistic query, records the tools the agent picked,
and asserts the final answer satisfies a validator.

Usage:
    python -m tests.verify_local_agent_e2e               # all tests
    python -m tests.verify_local_agent_e2e fs            # filter by group name
    python -m tests.verify_local_agent_e2e --quick       # skip slow audio/pdf
    python -m tests.verify_local_agent_e2e --model=deepseek/deepseek-v4-flash

Each LLM call ~6-8s on M4 + gemma4:latest. A typical 2-3 hop ReAct query is
~12-25s. Full sweep ~2-3 minutes. Cloud models (--model=openrouter/...) will
vary with network latency instead.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.local_agent_graph import run_local_agent_streaming  # noqa: E402
from clients.cloud_client import DEFAULT_CLOUD_MODEL

C = {
    "g": "\033[32m", "r": "\033[31m", "y": "\033[33m",
    "c": "\033[36m", "d": "\033[2m", "b": "\033[1m", "x": "\033[0m",
}


@dataclass
class TestCase:
    group: str
    name: str
    query: str
    expected_tools: list[str]            # at least one must appear
    forbidden_tools: list[str] = field(default_factory=list)
    validator: Callable[[str], bool] = lambda r: bool(r and len(r) > 5)
    min_response_chars: int = 5
    max_steps: int = 15
    skip_in_quick: bool = False


@dataclass
class Result:
    case: TestCase
    tools_called: list[str]
    final_response: str
    elapsed_s: float
    passed: bool
    failure_reason: str = ""


def run_case(case: TestCase, model: str = "gemma4") -> Result:
    tools_called: list[str] = []
    final_response = ""
    t0 = time.time()

    try:
        for event_type, payload in run_local_agent_streaming(
            query=case.query, model=model
        ):
            if event_type == "tool_call":
                tools_called.append(payload.get("name", ""))
            elif event_type == "final":
                final_response = payload or ""
    except Exception as e:
        return Result(
            case=case, tools_called=tools_called, final_response="",
            elapsed_s=time.time() - t0, passed=False,
            failure_reason=f"exception: {type(e).__name__}: {e}",
        )

    elapsed = time.time() - t0

    # Tool selection check
    if case.expected_tools and not any(t in tools_called for t in case.expected_tools):
        return Result(
            case=case, tools_called=tools_called, final_response=final_response,
            elapsed_s=elapsed, passed=False,
            failure_reason=f"expected one of {case.expected_tools}, got {tools_called}",
        )

    # Forbidden tool check
    forbidden_hit = [t for t in case.forbidden_tools if t in tools_called]
    if forbidden_hit:
        return Result(
            case=case, tools_called=tools_called, final_response=final_response,
            elapsed_s=elapsed, passed=False,
            failure_reason=f"forbidden tool called: {forbidden_hit}",
        )

    # Length check
    if len(final_response) < case.min_response_chars:
        return Result(
            case=case, tools_called=tools_called, final_response=final_response,
            elapsed_s=elapsed, passed=False,
            failure_reason=f"response too short ({len(final_response)} < {case.min_response_chars})",
        )

    # Custom validator
    if not case.validator(final_response):
        return Result(
            case=case, tools_called=tools_called, final_response=final_response,
            elapsed_s=elapsed, passed=False,
            failure_reason="validator returned False",
        )

    return Result(
        case=case, tools_called=tools_called, final_response=final_response,
        elapsed_s=elapsed, passed=True,
    )


# ----- Test fixtures -----

FIXTURE_DIR = Path("/tmp/g4l_e2e_fixtures")
FIXTURE_DIR.mkdir(exist_ok=True)
FIXTURE_TXT = FIXTURE_DIR / "agent_e2e_read_me.txt"
FIXTURE_TXT.write_text(
    "AGENT_E2E_FIXTURE_MARKER\n"
    "The capital of France is Paris.\n"
    "The square root of 144 is 12.\n"
)

AUDIO_FIXTURE = Path.home() / "Downloads" / "AUDIO-2026-05-07-13-06-31.m4a"
PDF_FIXTURE = Path.home() / "Downloads" / "Kalinov_Rozensky_Resume_Modern_Dark.pdf"


# ----- Test cases -----

def _resp_contains(needle: str):
    return lambda r: needle.lower() in r.lower()


CASES: list[TestCase] = [
    # ----- filesystem read -----
    TestCase(
        group="fs",
        name="list /tmp",
        query="list the files in the /tmp directory",
        expected_tools=["list_directory", "bash_exec"],
    ),
    TestCase(
        group="fs",
        name="read fixture file",
        query=f"read the file at {FIXTURE_TXT} and tell me what's in it",
        expected_tools=["read_file", "read_document"],
        validator=_resp_contains("paris"),
    ),
    TestCase(
        group="fs",
        name="find file by name (recursive)",
        query="find files matching AUDIO-2026 in my Downloads folder",
        expected_tools=["fd_find", "find_files"],
        forbidden_tools=[],  # list_directory is also acceptable here
    ),

    # ----- filesystem write -----
    TestCase(
        group="fs",
        name="write new file",
        query=f"create a new file at {FIXTURE_DIR}/agent_wrote_this.txt containing the single line 'hello from agent'",
        expected_tools=["write_file", "create_directory"],
        validator=lambda r: (FIXTURE_DIR / "agent_wrote_this.txt").exists(),
    ),

    # ----- shell -----
    TestCase(
        group="shell",
        name="bash echo",
        query="run a shell command that prints 'agent_shell_marker_xyz'",
        expected_tools=["bash_exec"],
        validator=_resp_contains("agent_shell_marker_xyz"),
    ),

    # ----- audio -----
    TestCase(
        group="audio",
        name="transcribe audio",
        query=f"transcribe the audio file at {AUDIO_FIXTURE}",
        expected_tools=["transcribe_audio"],
        min_response_chars=50,
        skip_in_quick=True,
    ),

    # ----- pdf -----
    TestCase(
        group="pdf",
        name="read pdf resume",
        query=f"read the PDF at {PDF_FIXTURE} and summarize what it is",
        expected_tools=["read_pdf", "read_document"],
        min_response_chars=50,
        skip_in_quick=True,
    ),
    TestCase(
        group="pdf",
        name="detect pdf form fields",
        query=f"detect the form fields in the PDF at {PDF_FIXTURE}",
        expected_tools=["detect_pdf_form_fields", "detect_form_fields"],
        skip_in_quick=True,
    ),
]


# ----- Runner -----

def main(argv: list[str]) -> int:
    quick = "--quick" in argv
    model = DEFAULT_CLOUD_MODEL
    for a in argv:
        if a.startswith("--model="):
            model = a.split("=", 1)[1]
    argv = [a for a in argv if not a.startswith("--model=")]
    name_filter = next((a for a in argv if not a.startswith("--")), None)

    cases = CASES
    if quick:
        cases = [c for c in cases if not c.skip_in_quick]
    if name_filter:
        cases = [c for c in cases if name_filter in c.group or name_filter in c.name]

    if not cases:
        print(f"{C['r']}No matching test cases.{C['x']}")
        return 1

    print(f"{C['b']}{C['c']}=== Local Agent E2E Verification ==={C['x']}")
    print(f"{C['d']}fixture dir: {FIXTURE_DIR}{C['x']}")
    print(f"{C['d']}model: {model}{C['x']}")
    print(f"{C['d']}running {len(cases)} cases (quick={quick}, filter={name_filter}){C['x']}\n")

    results: list[Result] = []
    for i, case in enumerate(cases, 1):
        label = f"[{i}/{len(cases)}] {case.group}/{case.name}"
        print(f"{C['y']}{label}{C['x']}")
        print(f"  {C['d']}query: {case.query[:90]}{'...' if len(case.query) > 90 else ''}{C['x']}")

        # Skip cases whose required fixture is missing
        if "transcribe" in case.name and not AUDIO_FIXTURE.exists():
            print(f"  {C['y']}SKIP — audio fixture missing{C['x']}\n")
            continue
        if "pdf" in case.group and not PDF_FIXTURE.exists():
            print(f"  {C['y']}SKIP — pdf fixture missing{C['x']}\n")
            continue

        r = run_case(case, model=model)
        results.append(r)
        mark = f"{C['g']}✓{C['x']}" if r.passed else f"{C['r']}✗{C['x']}"
        print(f"  {mark} tools={r.tools_called} elapsed={r.elapsed_s:.1f}s")
        if not r.passed:
            print(f"  {C['r']}reason:{C['x']} {r.failure_reason}")
            print(f"  {C['d']}response[:200]:{C['x']} {r.final_response[:200]!r}")
        else:
            print(f"  {C['d']}response[:120]:{C['x']} {r.final_response[:120]!r}")
        print()

    # Summary
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    total_time = sum(r.elapsed_s for r in results)

    print(f"{C['b']}=== Summary ==={C['x']}")
    print(
        f"  {C['g']}{passed} pass{C['x']}, "
        f"{C['r']}{failed} fail{C['x']} "
        f"({len(results)} run, total {total_time:.1f}s, "
        f"avg {total_time / max(len(results), 1):.1f}s)"
    )

    if failed:
        print(f"\n{C['r']}Failures:{C['x']}")
        for r in results:
            if not r.passed:
                print(f"  - {r.case.group}/{r.case.name}: {r.failure_reason}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
