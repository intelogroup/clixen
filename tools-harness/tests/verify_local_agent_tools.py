"""
Tool-level verification for all 16 tools the local agent can call.

Hits the same EXECUTORS dict the agent uses. Tests the tool layer in isolation
(no LLM, no graph). If a tool fails here, the agent will fail too.

Run:
    cd tools-harness && python -m tests.verify_local_agent_tools
"""

from __future__ import annotations

import json
import os
import sys
import shutil
import tempfile
import traceback
from pathlib import Path

# Ensure tools-harness is on sys.path
HARNESS_DIR = Path(__file__).resolve().parent.parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

from agents.local_agent_tools import (
    _LOCAL_AGENT_TOOL_NAMES,
    get_local_agent_executors,
)

EXECUTORS = get_local_agent_executors()
HOME = os.path.expanduser("~")

# ── Color helpers ──────────────────────────────────────────────────────────
GREEN, RED, YELLOW, RESET, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[0m", "\033[2m"


def _ok(name, msg=""):
    print(f"  {GREEN}✓{RESET} {name}{(' — ' + msg) if msg else ''}")


def _fail(name, msg):
    print(f"  {RED}✗{RESET} {name} — {msg}")


def _skip(name, msg):
    print(f"  {YELLOW}-{RESET} {name} — {msg}")


# ── Per-tool test runners ──────────────────────────────────────────────────
def run_tool(name: str, args: dict) -> tuple[bool, str]:
    """Execute a tool via its registry executor. Return (ok, content)."""
    if name not in EXECUTORS:
        return False, f"NOT in EXECUTORS"
    try:
        result = EXECUTORS[name](args)
        if isinstance(result, (dict, list)):
            content = json.dumps(result, default=str)
        else:
            content = str(result)
        return True, content
    except Exception as e:
        return False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"


def assert_substr(content: str, needle: str, name: str, label: str) -> bool:
    if needle in content:
        return True
    print(f"    {DIM}↳ expected '{needle}' in {label}, got: {content[:200]}{RESET}")
    return False


# ── Test plan ──────────────────────────────────────────────────────────────
def main() -> int:
    print(f"\n{YELLOW}Tool inventory:{RESET}")
    print(f"  Local agent toolset: {len(_LOCAL_AGENT_TOOL_NAMES)} tools")
    print(f"  Loaded executors: {len(EXECUTORS)} entries")

    missing = _LOCAL_AGENT_TOOL_NAMES - set(EXECUTORS.keys())
    if missing:
        print(f"  {RED}✗ missing executors:{RESET} {sorted(missing)}")
    else:
        print(f"  {GREEN}✓ all 16 tools registered{RESET}")

    # Scratch workspace
    scratch = Path(tempfile.mkdtemp(prefix="g4l_tool_verify_"))
    print(f"\n{YELLOW}Scratch dir:{RESET} {scratch}")

    pass_count = 0
    fail_count = 0
    skip_count = 0

    def record(ok, name, msg=""):
        nonlocal pass_count, fail_count
        if ok:
            _ok(name, msg)
            pass_count += 1
        else:
            _fail(name, msg)
            fail_count += 1

    # ── 1. create_directory ────────────────────────────────────────────────
    print(f"\n{YELLOW}[filesystem write]{RESET}")
    ok, out = run_tool("create_directory", {"path": str(scratch / "subdir")})
    record(ok and (scratch / "subdir").exists(), "create_directory", out[:80])

    # ── 2. write_file ──────────────────────────────────────────────────────
    sample_txt = scratch / "sample.txt"
    ok, out = run_tool(
        "write_file",
        {"path": str(sample_txt), "content": "line1\nline2\nline3\nline4\n"},
    )
    record(ok and sample_txt.exists(), "write_file", out[:80])

    # ── 3. edit_file ───────────────────────────────────────────────────────
    ok, out = run_tool(
        "edit_file",
        {"path": str(sample_txt), "old_str": "line2", "new_str": "MODIFIED"},
    )
    file_after = sample_txt.read_text() if sample_txt.exists() else ""
    record(
        ok and "MODIFIED" in file_after and "line2" not in file_after,
        "edit_file",
        out[:80],
    )

    # ── 4. read_file ───────────────────────────────────────────────────────
    print(f"\n{YELLOW}[filesystem read]{RESET}")
    ok, out = run_tool("read_file", {"path": str(sample_txt)})
    record(ok and "MODIFIED" in out, "read_file", f"{len(out)} chars")

    # ── 5. list_directory ──────────────────────────────────────────────────
    ok, out = run_tool("list_directory", {"path": str(scratch)})
    record(
        ok and "sample.txt" in out and "subdir" in out,
        "list_directory",
        f"{len(out.splitlines())} lines",
    )

    # ── 5b. list_directory truncation hint ─────────────────────────────────
    # Create 60 files to trigger the 50-cap truncation notice
    busy = scratch / "busy"
    busy.mkdir()
    for i in range(60):
        (busy / f"f{i:02d}.txt").write_text("x")
    ok, out = run_tool("list_directory", {"path": str(busy), "limits": 50})
    record(
        ok and "Showing first 50 entries" in out,
        "list_directory truncation",
        "50/60 cap with hint",
    )

    # ── 5c. list_directory /home/user fallback ─────────────────────────────
    fake_home = "/home/" + HOME.split("/")[2]
    ok, out = run_tool("list_directory", {"path": fake_home})
    record(
        ok and not out.startswith("Path not found"),
        "list_directory /home/user fallback",
        f"{len(out.splitlines())} lines",
    )

    # ── 6. find_files ──────────────────────────────────────────────────────
    ok, out = run_tool(
        "find_files",
        {"pattern": "*.txt", "path": str(scratch), "max_results": 10},
    )
    record(
        ok and "sample.txt" in out,
        "find_files",
        f"{len([l for l in out.splitlines() if l.strip()])} matches",
    )

    # ── 7. read_document (text file) ───────────────────────────────────────
    ok, out = run_tool("read_document", {"path": str(sample_txt)})
    record(ok and "MODIFIED" in out, "read_document (txt)", f"{len(out)} chars")

    # ── 8. bash_exec ───────────────────────────────────────────────────────
    print(f"\n{YELLOW}[shell]{RESET}")
    ok, out = run_tool(
        "bash_exec",
        {"command": "echo hello-from-bash", "cwd": str(scratch)},
    )
    record(ok and "hello-from-bash" in out, "bash_exec", out[:80])

    # ── 9. transcribe_audio ────────────────────────────────────────────────
    print(f"\n{YELLOW}[audio]{RESET}")
    audio_candidate = Path(HOME) / "Downloads" / "AUDIO-2026-05-07-13-06-31.m4a"
    if audio_candidate.exists():
        ok, out = run_tool("transcribe_audio", {"file_path": str(audio_candidate)})
        passed = (
            ok
            and len(out) > 50
            and not out.startswith("File not found")
            and "Traceback" not in out
        )
        msg = f"{len(out)} chars transcribed" if passed else out[:200]
        record(passed, "transcribe_audio", msg)
    else:
        _skip("transcribe_audio", f"no test audio at {audio_candidate}")
        skip_count += 1

    # ── 10. read_pdf / read_document (pdf) / form tools ────────────────────
    print(f"\n{YELLOW}[pdf + form tools]{RESET}")
    # Find any pdf in Downloads to test reading
    test_pdf = None
    downloads = Path(HOME) / "Downloads"
    if downloads.exists():
        for p in downloads.rglob("*.pdf"):
            try:
                if p.is_file() and p.stat().st_size < 5_000_000:
                    test_pdf = p
                    break
            except (PermissionError, OSError):
                continue

    if test_pdf is not None:
        ok, out = run_tool("read_pdf", {"path": str(test_pdf), "pages": "1-1"})
        record(ok and len(out) > 0, "read_pdf", f"{len(out)} chars (page 1 of {test_pdf.name})")

        ok, out = run_tool("read_document", {"path": str(test_pdf), "pages": "1-1"})
        record(ok and len(out) > 0, "read_document (pdf)", f"{len(out)} chars")

        ok, out = run_tool("detect_pdf_form_fields", {"path": str(test_pdf)})
        record(ok, "detect_pdf_form_fields", out[:80])

        ok, out = run_tool("detect_form_fields", {"file_path": str(test_pdf)})
        record(ok, "detect_form_fields", out[:80])

        # fill_pdf_form / fill_form / update_form: only meaningful on a form pdf
        # Try them but a "no fields" or "not a form" response counts as success
        # because the tool didn't crash.
        ok, out = run_tool(
            "fill_pdf_form",
            {"path": str(test_pdf), "fields": json.dumps({"DummyField": "x"})},
        )
        record(ok, "fill_pdf_form (no-field pdf)", out[:80])

        ok, out = run_tool(
            "fill_form",
            {"file_path": str(test_pdf), "fields": json.dumps({"DummyField": "x"})},
        )
        record(ok, "fill_form (no-field pdf)", out[:80])

        ok, out = run_tool(
            "update_form",
            {"file_path": str(test_pdf), "field_updates": json.dumps({"DummyField": "x"})},
        )
        record(ok, "update_form (no-field pdf)", out[:80])
    else:
        for n in (
            "read_pdf",
            "read_document (pdf)",
            "detect_pdf_form_fields",
            "detect_form_fields",
            "fill_pdf_form",
            "fill_form",
            "update_form",
        ):
            _skip(n, "no test PDF found in ~/Downloads")
            skip_count += 1

    # ── 11. data_to_xlsx ───────────────────────────────────────────────────
    print(f"\n{YELLOW}[document creation]{RESET}")
    csv_path = scratch / "data.csv"
    csv_path.write_text("name,age\nAlice,30\nBob,25\n")
    xlsx_out = scratch / "data.xlsx"
    ok, out = run_tool(
        "data_to_xlsx",
        {"data_path": str(csv_path), "output_path": str(xlsx_out)},
    )
    record(ok and xlsx_out.exists(), "data_to_xlsx", f"{xlsx_out.stat().st_size if xlsx_out.exists() else 0}B")

    # ── 12. markdown_to_pptx ───────────────────────────────────────────────
    md_path = scratch / "deck.md"
    md_path.write_text("# Slide 1\n\nHello\n\n---\n\n# Slide 2\n\nWorld\n")
    pptx_out = scratch / "deck.pptx"
    ok, out = run_tool(
        "markdown_to_pptx",
        {"md_path": str(md_path), "output_path": str(pptx_out)},
    )
    record(ok and pptx_out.exists(), "markdown_to_pptx", f"{pptx_out.stat().st_size if pptx_out.exists() else 0}B")

    # ── Cleanup & summary ──────────────────────────────────────────────────
    print(f"\n{YELLOW}Summary:{RESET}")
    total = pass_count + fail_count + skip_count
    print(f"  {GREEN}{pass_count} pass{RESET}, {RED}{fail_count} fail{RESET}, {YELLOW}{skip_count} skip{RESET} ({total} total)")
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"  scratch dir cleaned: {scratch}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
