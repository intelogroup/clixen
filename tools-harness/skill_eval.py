"""
Skill-injection eval for the LangGraph local agent (agents/local_agent_graph.py).

Answers the actual question: does injecting a skills_hub-style recipe into the
system prompt change task outcomes on a genuinely multi-step task, vs. the same
query with no skill prompt (pre-wiring free-planning behavior)? Not comparable
to gaia_eval.py — that path (harness.run force_local_agent=True) never calls
match_skill() at all.

The task: 3 budget figures scattered across 3 files in nested subfolders, sum
them, and write the total to a new file. This needs several ordered steps
(locate all 3 files -> read each -> compute -> write_file) — exactly the shape
where a weak local model tends to stop early (answers from 1-2 files) or skips
the write step entirely. A custom test-only Skill (not added to skills_data/)
is patched into match_skill() to prescribe the exact steps; a matched skill_id
is not required — this exercises the same skill_prompt injection wiring that
matched production skills use, just with a task-shaped recipe designed to fail
under free-planning.

Each condition runs N trials (gemma4 is flaky — a single trial isn't a signal).

Usage: ./.venv/bin/python tools-harness/skill_eval.py
"""
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import skills_hub
from agents.local_agent_graph import run_local_agent

QUERY = (
    "Find all budget files under this folder, sum the budget figures in them, "
    "and write the total to total.txt in this folder."
)

FILES = {
    "dept_a/budget.txt": "Q3 Budget Line: $18,500",
    "dept_b/notes/budget.txt": "Q3 Budget Line: $22,300",
    "dept_c/budget.txt": "Q3 Budget Line: $9,200",
}
EXPECTED_TOTAL = 18500 + 22300 + 9200  # 50000

TEST_SKILL = skills_hub.Skill(
    id="test.sum_budgets",
    name="Sum Scattered Budget Files",
    description="Locate all budget files under a folder, sum figures, write total to a file",
    category="test",
    tools=["find_files", "read_file", "write_file"],
    system_prompt=(
        "STEP 1: Call find_files to locate every file matching *budget*.txt under the project root "
        "(search recursively, do not stop after the first match).\n"
        "STEP 2: Call read_file on EACH file found — do not skip any.\n"
        "STEP 3: Sum every dollar figure found across all files.\n"
        "STEP 4: Call write_file(path='total.txt', content=<the sum as a plain number>) to save the result.\n"
        "STEP 5: Report the total to the user.\n"
        "Do not answer from partial data — if you have not read every file found in STEP 1, go back and read them."
    ),
    trigger_keywords=["sum budget", "budget files", "total budget"],
)

# Harder scenario: same budget-summing shape, but 4 files (one nested two levels
# deep) and a skill.tools list that deliberately omits bash_exec — a weak model
# asked to "sum figures" sometimes reaches for bash_exec/run_python to compute the
# sum instead of doing it inline. Exercises skill-scoped tool restriction (item 1
# of the IBM-practices plan) end-to-end through a live model, not just the unit-
# level allow-list check in tests/test_local_agent_complex.py.
QUERY_RESTRICTED = (
    "Find all budget files under this folder (check nested subfolders too), sum the "
    "budget figures in them, and write the total to total.txt in this folder."
)
FILES_RESTRICTED = {
    "region_a/budget.txt": "Q4 Budget Line: $11,000",
    "region_b/sub/deep/budget.txt": "Q4 Budget Line: $7,750",
    "region_c/budget.txt": "Q4 Budget Line: $3,250",
    "region_d/misc/budget.txt": "Q4 Budget Line: $2,000",
}
EXPECTED_TOTAL_RESTRICTED = 11000 + 7750 + 3250 + 2000  # 24000

TEST_SKILL_RESTRICTED = skills_hub.Skill(
    id="test.sum_budgets_restricted",
    name="Sum Scattered Budget Files (No Shell)",
    description="Locate all budget files under a folder (including nested), sum figures without shell tools, write total",
    category="test",
    tools=["find_files", "read_file", "write_file"],  # bash_exec/run_python deliberately excluded
    system_prompt=(
        "STEP 1: Call find_files to locate every file matching *budget*.txt under the project root, "
        "recursively including nested subfolders — do not stop after the first match.\n"
        "STEP 2: Call read_file on EACH file found — do not skip any, including deeply nested ones.\n"
        "STEP 3: Sum every dollar figure found across all files yourself — do not call bash_exec or "
        "run_python to compute the sum, they are not available for this task.\n"
        "STEP 4: Call write_file(path='total.txt', content=<the sum as a plain number>) to save the result.\n"
        "STEP 5: Report the total to the user.\n"
        "Do not answer from partial data — if you have not read every file found in STEP 1, go back and read them."
    ),
    trigger_keywords=["sum budget", "budget files", "total budget"],
)

N_TRIALS = 3


def run_trial(project_root: Path, force_skill_off: bool, query: str, skill, expected_total: int) -> tuple[bool, bool, str]:
    """Returns (answer_correct, file_written_correct, answer_text)."""
    total_path = project_root / "total.txt"
    if total_path.exists():
        total_path.unlink()

    match_fn = (lambda q: None) if force_skill_off else (lambda q: skill)
    with patch.object(skills_hub, "match_skill", side_effect=match_fn):
        answer = run_local_agent(
            query, model="gemma4:12b", task="document",
            project_root=str(project_root), max_steps=10,
        )

    answer_ok = str(expected_total) in answer.replace(",", "")
    file_ok = total_path.exists() and str(expected_total) in total_path.read_text().replace(",", "")
    return answer_ok, file_ok, answer


def run_scenario(label: str, query: str, files: dict, skill, expected_total: int):
    for condition, force_off in [("skill on", False), ("skill off", True)]:
        n_answer_ok = 0
        n_file_ok = 0
        for i in range(N_TRIALS):
            workdir = Path(tempfile.mkdtemp(prefix="skill-eval-hard-"))
            try:
                for rel, text in files.items():
                    p = workdir / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(text)
                answer_ok, file_ok, answer = run_trial(workdir, force_off, query, skill, expected_total)
                n_answer_ok += answer_ok
                n_file_ok += file_ok
                print(f"[{label}/{condition}] trial {i+1}: answer_ok={answer_ok} file_ok={file_ok} answer={answer[:100]!r}")
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
        print(f"[{label}/{condition}] answer correct: {n_answer_ok}/{N_TRIALS}, total.txt correct: {n_file_ok}/{N_TRIALS}\n")


def main():
    run_scenario("baseline", QUERY, FILES, TEST_SKILL, EXPECTED_TOTAL)
    run_scenario("restricted-no-shell", QUERY_RESTRICTED, FILES_RESTRICTED, TEST_SKILL_RESTRICTED, EXPECTED_TOTAL_RESTRICTED)


if __name__ == "__main__":
    main()
