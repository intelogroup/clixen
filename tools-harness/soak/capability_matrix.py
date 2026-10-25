#!/usr/bin/env python3
"""Capability scorecard for the orb: one line per dimension, with the suites that prove it.

Copied in spirit from the cloud repo's `soak/capability_matrix.py`, including its
best rule: **a dimension with no suite prints NOT TESTED rather than being
skipped**, so a gap in the evidence stays visible instead of quietly disappearing
from the report. That rule exists because "we didn't test it" and "we tested it and
it passed" used to look identical in a green test run.

    python soak/capability_matrix.py            # run everything, print the scorecard
    python soak/capability_matrix.py --json     # machine-readable rows
    python soak/capability_matrix.py --only Voice,Durability

This is the scripted layer (pytest, $0, deterministic). The measured layer — real
screens, real models, real latency — is `scripts/voice_latency.py` and
`scripts/injection_eval.py`, which need a live core and are not CI jobs.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# dimension -> test files (relative to tools-harness/tests/)
DIMENSIONS: dict[str, list[str]] = {
    "Screen control": ["test_ax_label_and_visibility.py", "test_eval_screen_actions_defs.py",
                       "voice/test_screen_epoch.py"],
    "Web actions": ["test_fetch_url.py", "voice/test_orb_toolset.py", "test_wikipedia_refs.py", "test_tabs_instances.py", "test_orb_scrapling.py", "test_browser_ambiguity.py"],
    "Finder / user files": ["test_finder_actions.py"],
    "Agent workspace": ["test_workspace.py"],
    "Memory semantics": ["voice/test_memory_v2.py"],
    "Injection resistance": ["test_injection_fence.py"],
    "Tool governance": ["test_action_class.py", "test_confirmation_store.py"],
    "Durability (journal)": ["test_step_journal.py"],
    "Screen text (OCR)": ["test_ocr_engines.py"],
    "Image understanding": ["test_vision_analyze.py"],
    "Machine, clipboard, files": ["test_orb_machine_tools.py"],
    "Tool demand (promotion gate)": ["test_tool_demand.py"],
    "Skills (procedures on demand)": ["test_skills.py"],
    "Popups and consent banners": ["test_consent_banners.py"],
    "Console capture": ["test_console_buffer.py"],
    "Extension bridge (dev)": ["test_ext_bridge.py", "test_extension_probe_js.py"],
    "Voice pipeline": ["voice/test_vad.py", "voice/test_session.py", "voice/test_streaming_asr.py"],
}

# Dimensions we know are unproven by a suite. Listed on purpose: the scorecard is
# also a to-do list, and hiding these would make the product look better tested
# than it is.
KNOWN_GAPS: dict[str, str] = {
    "Latency budget": "CI asserts the property (first clip before the stream ends: tests/voice/test_agent.py, test_tts_stream.py); the p50 number is only measured live by scripts/voice_latency.py against a real model and TTS, which CI cannot call",
    "Long-run soak (hours)": "jobs ran 42 min in production; no scripted soak",
    "Live human-input events": "no global event tap exists — polled at 3 s by tools/screen_watch.py",
}


def _run(files: list[str]) -> tuple[str, str]:
    existing = [f for f in files if (ROOT / "tests" / f).exists()]
    missing = [f for f in files if f not in existing]
    if not existing:
        return "NOT TESTED", "no suite"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *[str(ROOT / "tests" / f) for f in existing], "-q", "--no-header"],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
    )
    tail = (proc.stdout or proc.stderr).strip().splitlines()
    summary = tail[-1] if tail else f"exit {proc.returncode}"
    status = "PASS" if proc.returncode == 0 else "FAIL"
    if missing:
        summary += f"  (missing: {', '.join(missing)})"
    return status, summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    wanted = [d.strip().lower() for d in args.only.split(",") if d.strip()]
    rows = []
    for name, files in DIMENSIONS.items():
        if wanted and name.lower() not in wanted:
            continue
        status, detail = _run(files)
        rows.append({"dimension": name, "status": status, "detail": detail})
    for name, why in KNOWN_GAPS.items():
        if wanted and name.lower() not in wanted:
            continue
        rows.append({"dimension": name, "status": "NOT TESTED", "detail": why})

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        width = max(len(r["dimension"]) for r in rows)
        for r in rows:
            print(f"{r['status']:10s}  {r['dimension']:{width}s}  {r['detail']}")
    return 0 if all(r["status"] != "FAIL" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
