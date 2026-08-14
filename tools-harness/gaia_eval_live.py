"""
Live GAIA Level-1 validation runner — same prompt convention / scorer as
gaia_eval.py (smoke fixtures), but against the real 53-task gated dataset
(gaia-benchmark/GAIA, HF_TOKEN required) with real attachment files.

Writes incremental results to gaia_live_results.jsonl (resumable — skips
task_ids already present) so a multi-hour local-model run survives interrupts.

Usage: ./.venv/bin/python tools-harness/gaia_eval_live.py [--limit N]
"""
import argparse
import base64
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

sys.path.insert(0, str(Path(__file__).parent))
import harness  # noqa: E402
from gaia_eval import build_prompt, extract_final_answer, question_scorer  # noqa: E402

RESULTS_PATH = Path(__file__).parent / "gaia_live_results.jsonl"


def _load_done() -> set[str]:
    if not RESULTS_PATH.exists():
        return set()
    done = set()
    for line in RESULTS_PATH.read_text().splitlines():
        if line.strip():
            done.add(json.loads(line)["task_id"])
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    from datasets import load_dataset
    from huggingface_hub import snapshot_download

    token = os.environ["HF_TOKEN"]
    ds = load_dataset("gaia-benchmark/GAIA", "2023_level1", token=token)["validation"]
    repo_dir = Path(snapshot_download(
        repo_id="gaia-benchmark/GAIA", repo_type="dataset", token=token,
        allow_patterns=["2023/validation/*"],
    ))

    done = _load_done()
    rows = [r for r in ds if r["task_id"] not in done]
    if args.limit:
        rows = rows[: args.limit]
    print(f"{len(done)} already done, {len(rows)} to run")

    for row in rows:
        workdir = Path(tempfile.mkdtemp(prefix="gaia-live-"))
        try:
            attachment_hint = row.get("file_name") or None
            is_image = attachment_hint and Path(attachment_hint).suffix.lower() in _IMAGE_EXTS
            if attachment_hint:
                src = repo_dir / row["file_path"]
                if src.exists():
                    if not is_image:
                        shutil.copy(src, workdir / attachment_hint)
                else:
                    attachment_hint = None  # file listed but missing from snapshot
                    is_image = False

            if is_image:
                # Image attachments skip the local-agent (no vision tool wired into
                # its toolset) and go straight through harness's proven single-shot
                # CLOUD_VISION_MODEL path instead.
                b64 = base64.b64encode(src.read_bytes()).decode()
                raw, _model, _intent = harness.run(
                    build_prompt(row["Question"], None),
                    images=[b64],
                    chat_id=None,
                )
            else:
                prompt = build_prompt(row["Question"], attachment_hint)
                raw, _model, _intent = harness.run(
                    prompt,
                    force_local_agent=bool(attachment_hint),
                    project_root=str(workdir) if attachment_hint else None,
                    chat_id=None,
                )
            extracted = extract_final_answer(raw or "")
            correct = question_scorer(extracted, row["Final answer"])
            record = {
                "task_id": row["task_id"], "level": row["Level"], "correct": correct,
                "extracted": extracted, "gold": row["Final answer"], "had_file": bool(attachment_hint),
            }
        except Exception as e:
            record = {
                "task_id": row["task_id"], "level": row["Level"], "correct": False,
                "extracted": f"ERROR: {e}", "gold": row["Final answer"], "had_file": bool(attachment_hint),
            }
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        with RESULTS_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
        print(f"[{'PASS' if record['correct'] else 'FAIL'}] {record['task_id']}: "
              f"extracted={record['extracted']!r} gold={record['gold']!r}")

    all_records = [json.loads(l) for l in RESULTS_PATH.read_text().splitlines() if l.strip()]
    n_pass = sum(1 for r in all_records if r["correct"])
    print(f"\n{n_pass}/{len(all_records)} passed ({n_pass / len(all_records) * 100:.1f}%)")


if __name__ == "__main__":
    main()
