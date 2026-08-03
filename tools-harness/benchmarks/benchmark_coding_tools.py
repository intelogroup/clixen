#!/usr/bin/env python3
"""
Benchmark models on real tool use: context7 docs, tech/exa search,
filesystem operations, and git — including worktree.

Three categories (3 cases each, 15 pts max per case = 135 pts total):
  lookup     — context7 + exa tech_search: did the model call the tool and
                produce a grounded answer?
  filesystem — create dirs/files, edit, read, find, move: verified against disk
  git        — init, add, commit, log, worktree: verified via git commands

Models tested: qwen3.5:4b, qwen3:4b, gemma4
(set env CODING_MODELS="..." to override)

Test sandboxes (cleaned before each model):
  ~/developer/appytest1  — filesystem cases
  ~/developer/appytest2  — git cases
  ~/developer/appytest2-feature  — worktree target

Usage:
    python3 tools-harness/benchmark_coding_tools.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

from tools.registry import (
    CONTEXT7_SCHEMA,
    _EXA_TECH_SCHEMA,
    BASH_EXEC_SCHEMA,
    WRITE_FILE_SCHEMA,
    READ_FILE_SCHEMA,
    EDIT_FILE_SCHEMA,
    FIND_FILES_SCHEMA,
    LIST_DIR_SCHEMA,
    GIT_STATUS_SCHEMA,
    GIT_ADD_SCHEMA,
    GIT_COMMIT_SCHEMA,
    GIT_LOG_SCHEMA,
    GIT_WORKTREE_SCHEMA,
)
import clients.ollama_client as oc

MODELS = [
    m.strip()
    for m in os.environ.get("CODING_MODELS", "qwen3.5:4b,qwen3:4b,gemma4").split(",")
    if m.strip()
]

HOME = Path.home()
TEST1 = str(HOME / "developer/appytest1")
TEST2 = str(HOME / "developer/appytest2")
TEST2_WT = str(HOME / "developer/appytest2-feature")

MAX_ROUNDS = 10

# ---------------------------------------------------------------------------
# Tool schema sets
# ---------------------------------------------------------------------------
LOOKUP_TOOLS = [CONTEXT7_SCHEMA, _EXA_TECH_SCHEMA]
FS_TOOLS = [
    BASH_EXEC_SCHEMA, WRITE_FILE_SCHEMA, READ_FILE_SCHEMA,
    EDIT_FILE_SCHEMA, FIND_FILES_SCHEMA, LIST_DIR_SCHEMA,
]
GIT_TOOLS = [
    BASH_EXEC_SCHEMA, GIT_STATUS_SCHEMA, GIT_ADD_SCHEMA,
    GIT_COMMIT_SCHEMA, GIT_LOG_SCHEMA, GIT_WORKTREE_SCHEMA,
]


# ---------------------------------------------------------------------------
# Sandbox management
# ---------------------------------------------------------------------------
def reset_sandboxes():
    for p in [TEST1, TEST2, TEST2_WT]:
        if os.path.exists(p):
            shutil.rmtree(p)
    os.makedirs(TEST1, exist_ok=True)
    # TEST2 and TEST2_WT are created by the cases themselves


def _sh(cmd: str, cwd: str = None) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    return (r.stdout + r.stderr).strip()


# ---------------------------------------------------------------------------
# Run one case through the tool loop, capturing tool call log from stdout
# ---------------------------------------------------------------------------
def run_case_llm(model: str, query: str, tools: list, system_prompt: str = None) -> tuple[str, list[str], float]:
    """
    Returns (response_text, tools_called_list, elapsed_seconds).
    tools_called_list entries are like "get_library_docs" or "bash_exec".
    """
    # Cap token output to prevent qwen3 thinking-trace runaway.
    # qwen3:4b writes full reasoning in content even with think=False.
    # 512 tokens is enough for a tool call JSON or a concise final answer.
    _orig_run_local = oc._run_local

    def _patched_run_local(mdl, msgs, t, temperature=None):
        resp, elapsed = _orig_run_local(mdl, msgs, t, temperature=temperature)
        return resp, elapsed

    # Inject num_predict via options monkey-patch for qwen3 models
    _is_qwen = model.startswith("qwen3")

    if _is_qwen:
        _orig_client_chat = oc._get_client().chat

        def _capped_chat(**kwargs):
            opts = kwargs.get("options") or {}
            # 1500: enough for thinking preamble + tool call JSON (< 200 tokens)
            # without unlimited runaway on the final answer turn
            opts.setdefault("num_predict", 1500)
            kwargs["options"] = opts
            return _orig_client_chat(**kwargs)

        oc._get_client().chat = _capped_chat

    buf = io.StringIO()
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(buf):
            response = oc.chat(
                user_message=query,
                tools=tools,
                model=model,
                system_prompt=system_prompt,
                max_rounds=MAX_ROUNDS,
            )
    finally:
        if _is_qwen:
            oc._get_client().chat = _orig_client_chat
    elapsed = time.time() - t0
    log = buf.getvalue()
    # parse "[tool] fn_name(...)" and "[tool/text] fn_name(...)" lines
    called = []
    for line in log.splitlines():
        if line.startswith("[tool"):
            parts = line.split("]", 1)
            if len(parts) > 1:
                fn = parts[1].strip().split("(")[0].strip()
                if fn:
                    called.append(fn)
    return response, called, elapsed


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------
def verify_file_exists(path: str) -> tuple[bool, str]:
    ok = os.path.isfile(path)
    return ok, f"file {'exists' if ok else 'MISSING'}: {path}"


def verify_file_contains(path: str, text: str) -> tuple[bool, str]:
    try:
        content = Path(path).read_text()
        ok = text.lower() in content.lower()
        return ok, f"'{text}' {'found' if ok else 'NOT found'} in {path}"
    except FileNotFoundError:
        return False, f"file MISSING: {path}"


def verify_dir_exists(path: str) -> tuple[bool, str]:
    ok = os.path.isdir(path)
    return ok, f"dir {'exists' if ok else 'MISSING'}: {path}"


def verify_git_log(repo: str, message: str) -> tuple[bool, str]:
    out = _sh("git log --oneline", cwd=repo)
    ok = message.lower() in out.lower()
    return ok, f"git log {'has' if ok else 'MISSING'} '{message}': {out[:120]}"


def verify_git_worktree(repo: str, branch: str) -> tuple[bool, str]:
    out = _sh("git worktree list", cwd=repo)
    ok = branch.lower() in out.lower()
    return ok, f"worktree {'found' if ok else 'MISSING'} '{branch}': {out[:200]}"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
MAX_PTS = 15

def score_case(
    response: str,
    tools_called: list[str],
    case: dict,
    verifications: list[tuple[bool, str]],
) -> tuple[int, list[str]]:
    notes = []
    score = 0
    lowered = response.lower() if response else ""

    # 1. Expected tools called (+2 each, max 6)
    expected_tools = case.get("expected_tools", [])
    tool_hits = sum(1 for t in expected_tools if t in tools_called)
    tool_pts = min(6, tool_hits * 2)
    score += tool_pts
    missing = [t for t in expected_tools if t not in tools_called]
    if missing:
        notes.append(f"tools_not_called={missing}")

    # 2. Filesystem / git verifications (+2 each, max 6)
    for ok, msg in verifications:
        if ok:
            score += 2
        else:
            notes.append(msg)

    # 3. Answer quality terms (+1 each, max 3)
    for term in case.get("answer_terms", []):
        if term.lower() in lowered:
            score += 1
        else:
            notes.append(f"missing_term={term!r}")

    return min(MAX_PTS, score), notes


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
CASES = [
    # ── LOOKUP: context7 + tech_search ──────────────────────────────────────
    {
        "name": "ctx7_fastapi_background",
        "category": "lookup",
        "tools": LOOKUP_TOOLS,
        "query": (
            "Use get_library_docs to look up FastAPI BackgroundTasks. "
            "Then give me a minimal working Python example using BackgroundTasks."
        ),
        "expected_tools": ["get_library_docs"],
        "verifications": [],
        "answer_terms": ["fastapi", "background"],
    },
    {
        "name": "exa_httpx_latest",
        "category": "lookup",
        "tools": LOOKUP_TOOLS,
        "query": (
            "Use tech_search to find the latest stable release of the httpx Python "
            "library and what changed in it."
        ),
        "expected_tools": ["tech_search"],
        "verifications": [],
        "answer_terms": ["httpx"],
    },
    {
        "name": "ctx7_plus_exa_combo",
        "category": "lookup",
        "tools": LOOKUP_TOOLS,
        "query": (
            "First use get_library_docs to look up pytest fixtures. "
            "Then use tech_search to find the latest pytest version. "
            "Summarise both in a short answer."
        ),
        "expected_tools": ["get_library_docs", "tech_search"],
        "verifications": [],
        "answer_terms": ["pytest", "fixture"],
    },

    # ── FILESYSTEM ───────────────────────────────────────────────────────────
    {
        "name": "create_read_file",
        "category": "filesystem",
        "tools": FS_TOOLS,
        # Pre-setup: guarantee the src dir exists so the model only needs write+read
        "setup": lambda: _sh(f"mkdir -p {TEST1}/src"),
        "query": (
            f"Write a file at {TEST1}/src/hello.py with this exact content:\n"
            "def greet(name): return f'Hello, {name}!'\n\n"
            f"Then read {TEST1}/src/hello.py back and confirm the content to me."
        ),
        "expected_tools": ["write_file", "read_file"],
        "verifications": [
            lambda: verify_file_exists(f"{TEST1}/src/hello.py"),
            lambda: verify_file_contains(f"{TEST1}/src/hello.py", "greet"),
        ],
        "answer_terms": ["greet", "hello"],
    },
    {
        "name": "edit_and_find",
        "category": "filesystem",
        "tools": FS_TOOLS,
        # Pre-setup: create hello.py so edit_file has something to work on
        "setup": lambda: (
            _sh(f"mkdir -p {TEST1}/src"),
            Path(f"{TEST1}/src/hello.py").write_text("def greet(name): return f'Hello, {name}!'\n"),
        ),
        "query": (
            f"Open {TEST1}/src/hello.py and add a second function at the bottom:\n"
            "def add(a, b): return a + b\n\n"
            f"Then use find_files to find all .py files under {TEST1} and list them."
        ),
        "expected_tools": ["edit_file", "find_files"],
        "verifications": [
            lambda: verify_file_contains(f"{TEST1}/src/hello.py", "add"),
        ],
        "answer_terms": ["hello.py"],
    },
    {
        "name": "move_file_bash",
        "category": "filesystem",
        "tools": FS_TOOLS,
        # Pre-setup: ensure src/hello.py exists
        "setup": lambda: (
            _sh(f"mkdir -p {TEST1}/src"),
            Path(f"{TEST1}/src/hello.py").write_text("def greet(name): return f'Hello, {name}!'\n"),
        ),
        "query": (
            f"Use bash_exec to do all of these in one command string:\n"
            f"mkdir -p {TEST1}/tests && cp {TEST1}/src/hello.py {TEST1}/tests/test_hello.py\n"
            f"Then list both {TEST1}/src and {TEST1}/tests to confirm what's there."
        ),
        "expected_tools": ["bash_exec"],
        "verifications": [
            lambda: verify_file_exists(f"{TEST1}/tests/test_hello.py"),
            lambda: verify_dir_exists(f"{TEST1}/tests"),
        ],
        "answer_terms": ["tests", "hello"],
    },

    # ── GIT ─────────────────────────────────────────────────────────────────
    {
        "name": "git_init_commit",
        "category": "git",
        "tools": GIT_TOOLS,
        # Pre-setup: create dir
        "setup": lambda: _sh(f"mkdir -p {TEST2}"),
        "query": (
            f"Run these steps using bash_exec:\n"
            f"1. cd {TEST2} && git init -b main\n"
            f"2. Then write the file {TEST2}/main.py with content: def hello(): return 'world'\n"
            f"3. Then git add . in {TEST2} and commit with message 'initial commit'.\n"
            "Use git_add and git_commit tools for the add and commit steps."
        ),
        "expected_tools": ["bash_exec", "git_add", "git_commit"],
        "verifications": [
            lambda: verify_file_exists(f"{TEST2}/main.py"),
            lambda: verify_git_log(TEST2, "initial commit"),
        ],
        "answer_terms": ["commit", "initial"],
    },
    {
        "name": "git_log_and_status",
        "category": "git",
        "tools": GIT_TOOLS,
        # Pre-setup: init repo + initial commit so log works
        "setup": lambda: (
            _sh(f"mkdir -p {TEST2}"),
            _sh(f"git -C {TEST2} init -b main"),
            Path(f"{TEST2}/main.py").write_text("def hello(): return 'world'\n"),
            _sh(f"git -C {TEST2} add . && git -C {TEST2} -c user.email=t@t.com -c user.name=T commit -m 'initial commit'"),
        ),
        "query": (
            f"In the git repo at {TEST2}:\n"
            f"Write a new file {TEST2}/utils.py with content: def add(a,b): return a+b\n"
            f"Then use git_add to stage it and git_commit with message 'add utils'.\n"
            f"Then call git_log to show the last 3 commits."
        ),
        "expected_tools": ["git_add", "git_commit", "git_log"],
        "verifications": [
            lambda: verify_file_exists(f"{TEST2}/utils.py"),
            lambda: verify_git_log(TEST2, "add utils"),
        ],
        "answer_terms": ["utils", "commit"],
    },
    {
        "name": "git_worktree",
        "category": "git",
        "tools": GIT_TOOLS,
        # Pre-setup: fully ready repo with at least one commit
        "setup": lambda: (
            _sh(f"rm -rf {TEST2} {TEST2_WT}"),
            _sh(f"mkdir -p {TEST2}"),
            _sh(f"git -C {TEST2} init -b main"),
            Path(f"{TEST2}/main.py").write_text("def hello(): return 'world'\n"),
            _sh(f"git -C {TEST2} add . && git -C {TEST2} -c user.email=t@t.com -c user.name=T commit -m 'initial commit'"),
        ),
        "query": (
            f"Use git_new_worktree to create a worktree for the repo at {TEST2}.\n"
            f"worktree_path={TEST2_WT}, branch=feature/tests.\n"
            f"Then use bash_exec to run: git -C {TEST2} worktree list\n"
            "Report what worktrees exist."
        ),
        "expected_tools": ["git_new_worktree"],
        "verifications": [
            lambda: verify_git_worktree(TEST2, "feature/tests"),
        ],
        "answer_terms": ["worktree", "feature"],
    },
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    categories = sorted({c["category"] for c in CASES})
    overall = []

    for model in MODELS:
        print(f"\n{'='*60}", flush=True)
        print(f"MODEL {model}", flush=True)
        print(f"{'='*60}", flush=True)

        reset_sandboxes()

        total = 0
        latencies = []
        cat_scores: dict[str, list[int]] = {cat: [] for cat in categories}

        for case in CASES:
            print(f"\n  CASE {case['name']} [{case['category']}]", flush=True)
            if "setup" in case:
                case["setup"]()
            response, tools_called, elapsed = run_case_llm(
                model, case["query"], case["tools"]
            )

            # Run verifications (callables)
            vresults = []
            for v in case.get("verifications", []):
                vresults.append(v())

            score, notes = score_case(response, tools_called, case, vresults)
            total += score
            latencies.append(elapsed)
            cat_scores[case["category"]].append(score)

            row = {
                "task": case["name"],
                "category": case["category"],
                "score": score,
                "max": MAX_PTS,
                "seconds": round(elapsed, 1),
                "tools_called": tools_called,
                "verifications": [msg for _, msg in vresults],
                "notes": notes,
                "reply_preview": (response or "")[:200],
            }
            print(json.dumps(row, sort_keys=True), flush=True)

        cat_summary = {
            cat: {
                "score": sum(scores),
                "max": len(scores) * MAX_PTS,
                "pct": round(100 * sum(scores) / (len(scores) * MAX_PTS), 1),
            }
            for cat, scores in cat_scores.items()
            if scores
        }
        row = {
            "model": model,
            "score": total,
            "max": len(CASES) * MAX_PTS,
            "pct": round(100 * total / (len(CASES) * MAX_PTS), 1),
            "avg_seconds": round(sum(latencies) / len(latencies), 2),
            "by_category": cat_summary,
        }
        overall.append(row)
        print("\nSUMMARY_ROW " + json.dumps(row, sort_keys=True), flush=True)

    print("\n\nFINAL_SUMMARY", flush=True)
    for row in sorted(overall, key=lambda r: (r["score"], -r["avg_seconds"]), reverse=True):
        print(json.dumps(row, sort_keys=True), flush=True)

    # cleanup
    for p in [TEST1, TEST2, TEST2_WT]:
        if os.path.exists(p):
            shutil.rmtree(p)
    print("\nSandboxes cleaned up.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
