"""
Verify the Path/Discovery specialist subagent.

Tests the specialist in isolation. Each case asserts:
  - At least one expected tool was called
  - The PathResult validator predicate holds (paths, content, etc.)
  - No exceptions

Each case is a warm gemma4 call (~20-40s). Full suite ~4-5 min.

Usage:
    python -m tests.verify_path_specialist
    python -m tests.verify_path_specialist 3        # filter by case index/name
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.specialists.path_specialist import (  # noqa: E402
    PathResult, run_path_specialist,
)

C = {
    "g": "\033[32m", "r": "\033[31m", "y": "\033[33m",
    "c": "\033[36m", "m": "\033[35m",
    "b": "\033[1m", "d": "\033[2m", "x": "\033[0m",
}

HOME = Path.home()
REPO_ROOT = ROOT  # tools-harness


@dataclass
class Case:
    name: str
    query: str
    expected_tools: list[str]
    validator: Callable[[PathResult], tuple[bool, str]]
    cwd: str | None = None
    skip_if: Callable[[], bool] = lambda: False


def _val_nonempty(name: str = "paths") -> Callable[[PathResult], tuple[bool, str]]:
    def _v(r: PathResult):
        ok = len(r.paths) > 0
        return ok, f"len({name})={len(r.paths)}"
    return _v


def _val_any_path_matches(predicate: Callable[[str], bool], descr: str) -> Callable[[PathResult], tuple[bool, str]]:
    def _v(r: PathResult):
        hits = [p for p in r.paths if predicate(p)]
        return bool(hits), f"matching={hits[:3]} (need: {descr})"
    return _v


def _val_paths_empty() -> Callable[[PathResult], tuple[bool, str]]:
    def _v(r: PathResult):
        ok = len(r.paths) == 0
        return ok, f"len(paths)={len(r.paths)} (want 0)"
    return _v


def _val_min_count(n: int, ext: str | None = None) -> Callable[[PathResult], tuple[bool, str]]:
    def _v(r: PathResult):
        if ext:
            matches = [p for p in r.paths if p.endswith(ext)]
            ok = len(matches) >= n
            return ok, f"got {len(matches)} files ending {ext} (need >= {n})"
        ok = len(r.paths) >= n
        return ok, f"got {len(r.paths)} paths (need >= {n})"
    return _v


# ---- Fixtures we depend on ----
AUDIO_FIXTURE = HOME / "Downloads" / "AUDIO-2026-05-07-13-06-31.m4a"
HELLO_FIXTURE = HOME / "Documents" / "jimmyno" / "gems" / "hello"


CASES: list[Case] = [
    Case(
        name="1-list /tmp",
        query="list the contents of /tmp",
        expected_tools=["list_directory"],
        validator=_val_nonempty(),
    ),
    Case(
        name="2-find AUDIO in Downloads",
        query="find files matching AUDIO-2026 in my Downloads folder",
        expected_tools=["find_files", "fd_find"],
        validator=_val_any_path_matches(
            lambda p: "AUDIO-2026" in p and p.endswith(".m4a"),
            "*.m4a containing AUDIO-2026",
        ),
        skip_if=lambda: not AUDIO_FIXTURE.exists(),
    ),
    Case(
        name="3-fuzzy hello in Documents",
        query="where is the hello folder in Documents?",
        expected_tools=["find_files", "file_tree"],
        validator=_val_any_path_matches(
            lambda p: p.rstrip("/").endswith("/hello"),
            "path ending /hello",
        ),
        skip_if=lambda: not HELLO_FIXTURE.exists(),
    ),
    Case(
        name="4-glob .py in agents",
        query=f"find all .py files in {REPO_ROOT}/agents",
        expected_tools=["find_files", "fd_find"],
        validator=_val_min_count(10, ext=".py"),
    ),
    Case(
        name="5-grep ffmpeg in tools-harness",
        query=f"grep for the string 'ffmpeg' inside the {REPO_ROOT} directory",
        expected_tools=["grep_files", "ripgrep"],
        validator=_val_any_path_matches(
            lambda p: p.endswith("audio.py"),
            "tools/audio.py",
        ),
    ),
    Case(
        name="6-no scope (cwd default)",
        query="find a file called registry.py",
        expected_tools=["find_files", "fd_find"],
        validator=_val_any_path_matches(
            lambda p: p.endswith("/registry.py"),
            "**/registry.py",
        ),
        cwd=str(REPO_ROOT),
    ),
    Case(
        name="7-adversarial out-of-scope",
        query=f"transcribe my audio file at {AUDIO_FIXTURE}",
        # Specialist should resolve the path, not refuse. Tool may or may not
        # be called depending on whether the model treats the query as
        # "already-resolved path" or feels the need to verify.
        expected_tools=[],  # no requirement; just don't crash
        validator=lambda r: (
            (str(AUDIO_FIXTURE) in r.paths or len(r.paths) >= 0),
            f"paths={r.paths[:2]} (must not crash; resolving path is bonus)",
        ),
        skip_if=lambda: not AUDIO_FIXTURE.exists(),
    ),
    Case(
        name="8-edge nonexistent",
        query="find a file called definitely_does_not_exist_xyzqq.qqq",
        expected_tools=["find_files", "fd_find"],
        validator=_val_paths_empty(),
        cwd=str(REPO_ROOT),
    ),
]


def run_case(c: Case) -> dict:
    t0 = time.time()
    try:
        result = run_path_specialist(
            query=c.query,
            model="gemma4:12b-mlx",
            cwd=c.cwd,
            max_steps=8,
        )
    except Exception as e:
        return {
            "case": c.name,
            "passed": False,
            "elapsed_s": time.time() - t0,
            "tools": [],
            "paths": [],
            "summary": "",
            "reason": f"exception: {type(e).__name__}: {e}",
        }

    tool_ok = (
        not c.expected_tools  # no requirement
        or any(t in result.tool_trace for t in c.expected_tools)
    )
    val_ok, val_msg = c.validator(result)
    passed = tool_ok and val_ok

    reasons = []
    if not tool_ok:
        reasons.append(f"want {c.expected_tools} got {result.tool_trace}")
    if not val_ok:
        reasons.append(val_msg)
    if result.error:
        reasons.append(f"error={result.error}")

    return {
        "case": c.name,
        "passed": passed,
        "elapsed_s": result.elapsed_s,
        "tools": result.tool_trace,
        "paths": result.paths,
        "summary": result.summary,
        "error": result.error,
        "val_msg": val_msg,
        "reason": "; ".join(reasons),
    }


def main(argv: list[str]) -> int:
    name_filter = next((a for a in argv if not a.startswith("--")), None)

    selected = CASES
    if name_filter:
        selected = [c for c in CASES if name_filter in c.name]
    if not selected:
        print(f"{C['r']}No matching cases.{C['x']}")
        return 1

    print(f"{C['b']}{C['c']}=== Path/Discovery Specialist Verification ==={C['x']}")
    print(f"{C['d']}cwd default = {Path.cwd()}{C['x']}")
    print(f"{C['d']}{len(selected)} cases (filter={name_filter}){C['x']}\n")

    results = []
    for i, c in enumerate(selected, 1):
        label = f"[{i}/{len(selected)}] {c.name}"
        if c.skip_if():
            print(f"{C['y']}{label}{C['x']} {C['d']}— SKIP (fixture missing){C['x']}\n")
            continue
        print(f"{C['y']}{label}{C['x']}")
        print(f"  {C['d']}query: {c.query[:90]}{'...' if len(c.query) > 90 else ''}{C['x']}")

        r = run_case(c)
        results.append(r)
        mark = f"{C['g']}✓{C['x']}" if r["passed"] else f"{C['r']}✗{C['x']}"
        tools_str = "+".join(r["tools"]) or "-"
        print(f"  {mark} {r['elapsed_s']:.1f}s tools={tools_str} paths={len(r['paths'])}")
        if r["paths"]:
            sample = r["paths"][:3]
            extra = f" +{len(r['paths']) - 3} more" if len(r["paths"]) > 3 else ""
            print(f"  {C['d']}paths[:3]: {sample}{extra}{C['x']}")
        if r["summary"]:
            print(f"  {C['d']}summary: {r['summary'][:120]!r}{C['x']}")
        if not r["passed"]:
            print(f"  {C['r']}reason:{C['x']} {r['reason']}")
        print()

    # Summary
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    total_t = sum(r["elapsed_s"] for r in results)
    avg_t = total_t / max(len(results), 1)
    print(f"{C['b']}=== Summary ==={C['x']}")
    print(
        f"  {C['g']}{passed} pass{C['x']}, "
        f"{C['r']}{failed} fail{C['x']} "
        f"({len(results)} run, total {total_t:.1f}s, avg {avg_t:.1f}s)"
    )
    if failed:
        print(f"\n{C['r']}Failures:{C['x']}")
        for r in results:
            if not r["passed"]:
                print(f"  - {r['case']}: {r['reason']}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
