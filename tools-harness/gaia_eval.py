"""
Standalone GAIA smoke runner for clixen's harness — mirrors atomic-agent's
eval-agents/gaia.eval.ts prompt convention and eval-agents/harness/score-gaia.ts
scorer (itself a port of the official gaia-benchmark/leaderboard scorer.py) so
results are directly comparable across agents.

Usage: ./.venv/bin/python tools-harness/gaia_eval.py
"""
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import harness  # noqa: E402

FIXTURES = Path.home() / "Developer/atomic-agent/eval-agents/datasets/gaia/fixtures/smoke-level1.json"

FINAL_ANSWER_RE = re.compile(r"FINAL\s+ANSWER\s*:\s*(.*)$", re.IGNORECASE)


def build_prompt(question: str, attachment_hint: str | None) -> str:
    prefix = (
        "You are solving a GAIA benchmark question. Use tools as needed "
        "(filesystem, web, documents). When finished, your last line MUST be "
        "exactly: FINAL ANSWER: <your answer> The answer must match GAIA "
        "rules: a short string, a number, or a comma-separated list — no "
        "extra explanation on that line."
    )
    attachment = f" Attached file: {attachment_hint} (in the project root — read it directly by name, do not guess a different folder)." if attachment_hint else ""
    return f"{prefix}{attachment} Question: {question}".strip()


def extract_final_answer(reply: str) -> str:
    lines = [l.strip() for l in reply.strip().splitlines() if l.strip()]
    last_match = None
    for i, line in enumerate(lines):
        m = FINAL_ANSWER_RE.search(line)
        if m:
            last_match = (m.group(1).strip(), i)
    if last_match:
        same_line, idx = last_match
        if same_line:
            return same_line.strip("\"'`")
        if idx + 1 < len(lines):
            return lines[idx + 1].strip("\"'`")
    return lines[-1].strip("\"'`") if lines else ""


def _is_float_str(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _normalize_number(s: str) -> float:
    for ch in ["$", "%", ","]:
        s = s.replace(ch, "")
    try:
        return float(s.strip())
    except ValueError:
        return float("inf")


def _normalize_str(s: str, remove_punct: bool = True) -> str:
    s = re.sub(r"\s", "", s).lower()
    if not remove_punct:
        return s
    return re.sub(r"[!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~]", "", s)


def question_scorer(model_answer: str | None, ground_truth: str) -> bool:
    ma = model_answer if model_answer is not None else "None"
    gt = ground_truth

    if _is_float_str(gt):
        return _normalize_number(ma) == float(gt)

    if "," in gt or ";" in gt:
        gt_elems = [p.strip() for p in re.split(r"[,;]", gt) if p.strip()]
        ma_elems = [p.strip() for p in re.split(r"[,;]", ma) if p.strip()]
        if len(gt_elems) != len(ma_elems):
            return False
        for ge, me in zip(gt_elems, ma_elems):
            if _is_float_str(ge):
                if _normalize_number(me) != float(ge):
                    return False
            elif _normalize_str(me, False) != _normalize_str(ge, False):
                return False
        return True

    return _normalize_str(ma) == _normalize_str(gt)


def main():
    rows = json.loads(FIXTURES.read_text())
    results = []

    for row in rows:
        workdir = Path(tempfile.mkdtemp(prefix="gaia-clixen-"))
        try:
            attachment_hint = row.get("file_name") or None
            if attachment_hint and row.get("fixture_file_text"):
                (workdir / attachment_hint).write_text(row["fixture_file_text"])

            prompt = build_prompt(row["Question"], attachment_hint)
            raw, _model, _intent = harness.run(
                prompt,
                force_local_agent=True,
                project_root=str(workdir),
                chat_id=None,
                intent="filesystem",  # skip classify_message()'s cloud call — pure local gemma test
            )
            extracted = extract_final_answer(raw or "")
            correct = question_scorer(extracted, row["Final answer"])
            results.append((row["task_id"], correct, extracted, row["Final answer"]))
            print(f"[{'PASS' if correct else 'FAIL'}] {row['task_id']}: extracted={extracted!r} gold={row['Final answer']!r}")
        except Exception as e:
            results.append((row["task_id"], False, f"ERROR: {e}", row["Final answer"]))
            print(f"[ERROR] {row['task_id']}: {e}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    n_pass = sum(1 for _, c, _, _ in results if c)
    print(f"\n{n_pass}/{len(results)} passed ({n_pass / len(results) * 100:.1f}%)")


if __name__ == "__main__":
    main()
