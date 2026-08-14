"""
Harder eval than gaia_eval.py's smoke set: real multi-file / long-doc tasks
requiring the local agent to combine filesystem + reading + cross-file
reasoning (policy+CSV violation math, needle-in-3000-word-doc, 5-file hour
aggregation, table+narrative derived math). Reuses gaia_eval.py's answer
extraction/scoring so results are comparable.

Usage: ./.venv/bin/python tools-harness/complex_retrieval_eval.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import harness  # noqa: E402
from gaia_eval import build_prompt, extract_final_answer, question_scorer  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "tests/fixtures/complex_retrieval"


def main():
    tasks = json.loads((FIXTURES_DIR / "tasks.json").read_text())
    results = []

    for task in tasks:
        workdir = FIXTURES_DIR / task["dir"]
        files = sorted(p.name for p in workdir.iterdir() if p.is_file())
        prompt = build_prompt(task["question"], ", ".join(files))
        try:
            raw, _model, _intent = harness.run(
                prompt,
                force_local_agent=True,
                project_root=str(workdir),
                chat_id=None,
                intent="filesystem",  # skip classify_message()'s cloud call — pure local test
            )
            extracted = extract_final_answer(raw or "")
            correct = question_scorer(extracted, task["final_answer"])
            results.append((task["task_id"], correct))
            print(f"[{'PASS' if correct else 'FAIL'}] {task['task_id']}: extracted={extracted!r} gold={task['final_answer']!r}")
        except Exception as e:
            results.append((task["task_id"], False))
            print(f"[ERROR] {task['task_id']}: {e}")

    n_pass = sum(1 for _, c in results if c)
    print(f"\n{n_pass}/{len(results)} passed ({n_pass / len(results) * 100:.1f}%)")


if __name__ == "__main__":
    main()
