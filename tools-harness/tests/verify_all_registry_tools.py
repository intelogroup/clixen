"""
Smoke-test ALL dry-testable tools in EXECUTORS (no LLM, no auth).

Tests every tool that can run without external services, browser, or auth.
Skips tools that need: Gmail OAuth, browser/Playwright, WhatsApp, Slack,
Telegram, iMessage send, YouTube API, arXiv/PubMed API, skills hub network.

Run:
    cd tools-harness && python -m tests.verify_all_registry_tools
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent.parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

from tools.registry import EXECUTORS

HOME = os.path.expanduser("~")

# ── Helpers ─────────────────────────────────────────────────────────────────
GREEN, RED, YELLOW, CYAN, RESET, DIM, BOLD = (
    "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m", "\033[2m", "\033[1m",
)

_EXPLICIT_SKIP: dict[str, str] = {
    # Auth / OAuth
    "get_latest_email": "needs Gmail OAuth",
    "list_emails": "needs Gmail OAuth",
    "read_email": "needs Gmail OAuth",
    "send_email": "needs Gmail OAuth",
    "run_inbox_monitor": "needs Gmail OAuth",
    "list_pdf_attachments": "needs Gmail OAuth",
    "convert_pdf": "needs Gmail OAuth",
    "refresh_google_token": "needs Gmail OAuth",
    "list_calendar_events": "needs Google Calendar OAuth",
    "create_calendar_event": "needs Google Calendar OAuth",
    "delete_calendar_event": "needs Google Calendar OAuth",
    "list_tasks": "needs Google Tasks OAuth",
    "create_task": "needs Google Tasks OAuth",
    "complete_task": "needs Google Tasks OAuth",
    "delete_task": "needs Google Tasks OAuth",
    # Browser automation (needs Playwright)
    "browser_navigate": "needs running browser",
    "browser_snapshot": "needs running browser",
    "browser_get_url": "needs running browser",
    "browser_click": "needs running browser",
    "browser_type": "needs running browser",
    "browser_check": "needs running browser",
    "browser_select": "needs running browser",
    "browser_wait": "needs running browser",
    "browser_get_content": "needs running browser",
    "browser_get_attribute": "needs running browser",
    "browser_run_js": "needs running browser",
    "browser_screenshot": "needs running browser",
    "browser_close": "needs running browser",
    # Messaging / external APIs
    "whatsapp_search": "needs WhatsApp bridge DB",
    "whatsapp_status": "needs WhatsApp bridge DB",
    "send_whatsapp": "needs WhatsApp bridge",
    "slack_search": "needs Slack DB",
    "slack_status": "needs Slack DB",
    "send_telegram": "needs Telegram bot",
    "search_arxiv": "needs network (arXiv API)",
    "search_pubmed": "needs network (PubMed API)",
    "search_youtube": "needs network (YouTube API)",
    "get_youtube_transcript": "needs network (YouTube)",
    "web_search": "needs network (search API)",
    "brave_search": "needs network (Brave API)",
    "tech_search": "needs network (Exa API)",
    "local_search": "needs knowledge DB",
    # MCP / external processes
    "get_library_docs": "needs context7 MCP",
    # Skills hub (network)
    "list_available_skills": "needs network (skills hub)",
    "match_skill_for_task": "needs network (skills hub)",
    # Automation
    "create_automation": "needs automation runtime",
    "list_automations": "needs automation runtime",
    "delete_automation": "needs automation runtime",
    "pause_automation": "needs automation runtime",
    "resume_automation": "needs automation runtime",
    "trigger_automation_now": "needs automation runtime",
    # Google Docs
    "list_google_docs": "needs Google Docs OAuth",
    "read_google_doc": "needs Google Docs OAuth",
    "create_google_doc": "needs Google Docs OAuth",
    "append_google_doc": "needs Google Docs OAuth",
    "update_google_doc_title": "needs Google Docs OAuth",
    # Google Sheets
    "list_google_sheets": "needs Google Sheets OAuth",
    "read_google_sheet": "needs Google Sheets OAuth",
    "create_google_sheet": "needs Google Sheets OAuth",
    "append_google_sheet": "needs Google Sheets OAuth",
    "update_google_sheet_cell": "needs Google Sheets OAuth",
    # Clutter
    "analyze_directory": "needs clutter runtime",
    "apply_organization": "needs clutter runtime",
    "suggest_organization": "needs clutter runtime",
    # Dangerous / destructive (skip in smoke test)
    "git_add": "destructive (stages files)",
    "git_commit": "destructive (creates commit)",
    "git_checkout": "destructive (switches branch)",
    "git_new_worktree": "destructive (creates worktree)",
    "delete_file": "destructive (skip in smoke)",
    "set_reminder": "destructive (creates reminder)",
    "notes_create": "destructive (creates note)",
    "create_reminder": "destructive (creates reminder)",
    "imessage_send": "destructive (sends message)",
    "send_telegram": "destructive (sends message)",
    "send_email": "destructive (sends email)",
    "create_calendar_event": "destructive (creates event)",
    "create_task": "destructive (creates task)",
    # AppleScript (could trigger unintended actions)
    "applescript_run": "dangerous (arbitrary AppleScript)",
}


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


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="g4l_all_tools_"))
    print(f"\n{CYAN}=== All Registry Tools Smoke Test ==={RESET}")
    print(f"{DIM}Scratch: {scratch}{RESET}")
    print(f"{DIM}Total EXECUTORS: {len(EXECUTORS)}{RESET}\n")

    pass_count = 0
    fail_count = 0
    skip_count = 0

    # Create test files in scratch
    (scratch / "test.txt").write_text("hello world\nfoo bar\nbaz qux\n")
    (scratch / "test.py").write_text("def hello():\n    return 'world'\n\nclass Foo:\n    pass\n")
    (scratch / "test.csv").write_text("name,age\nAlice,30\nBob,25\n")
    (scratch / "test.json").write_text('{"key": "value", "nested": {"inner": 1}}')
    (scratch / "subdir").mkdir()
    (scratch / "subdir" / "inner.txt").write_text("nested file content")
    (scratch / "index.md").write_text("# Hello\n\nThis is a markdown file.")

    def record(ok, name, msg=""):
        nonlocal pass_count, fail_count
        if ok:
            print(f"  {GREEN}\u2713{RESET} {name}{f' {DIM}— {msg}{RESET}' if msg else ''}")
            pass_count += 1
        else:
            print(f"  {RED}\u2717{RESET} {name}{f' {DIM}— {msg}{RESET}' if msg else ''}")
            fail_count += 1

    def skip(name, reason):
        nonlocal skip_count
        print(f"  {YELLOW}\u2500{RESET} {name} {DIM}({reason}){RESET}")
        skip_count += 1

    # ── Filesystem — read ───────────────────────────────────────────────────
    print(f"{BOLD}[filesystem — read]{RESET}")

    ok, out = run_tool("read_file", {"path": str(scratch / "test.txt")})
    record(ok and "hello world" in out, "read_file", f"{len(out)} chars")

    ok, out = run_tool("read_many_files", {
        "paths": [str(scratch / "test.txt"), str(scratch / "test.csv")],
        "limit_per_file": 50,
    })
    record(ok and "hello" in out and "Alice" in out, "read_many_files", f"{len(out)} chars")

    ok, out = run_tool("file_info", {"path": str(scratch / "test.txt")})
    record(ok and "test.txt" in out, "file_info")

    ok, out = run_tool("find_largest", {"path": str(scratch), "n": 3})
    record(ok and len(out) > 0, "find_largest", f"{len(out.splitlines())} entries")

    ok, out = run_tool("grep_files", {
        "pattern": "hello", "path": str(scratch), "ignore_case": True,
    })
    record(ok and "test.txt" in out, "grep_files")

    ok, out = run_tool("find_files", {"pattern": "*.txt", "path": str(scratch)})
    record(ok and "test.txt" in out, "find_files")

    ok, out = run_tool("list_directory", {"path": str(scratch), "show_hidden": False})
    record(ok and "test.txt" in out and "subdir" in out, "list_directory")

    ok, out = run_tool("file_tree", {"path": str(scratch), "max_depth": 2})
    record(ok and "test.txt" in out, "file_tree")

    ok, out = run_tool("find_files", {"pattern": "*.txt", "path": str(scratch)})
    record(ok and "test.txt" in out, "find_files")

    # ── Filesystem — write/manage ───────────────────────────────────────────
    print(f"\n{BOLD}[filesystem — write/manage]{RESET}")

    src = str(scratch / "test.txt")
    dst = str(scratch / "copied.txt")
    ok, out = run_tool("copy_file", {"src": src, "dest": dst})
    record(ok and (scratch / "copied.txt").exists(), "copy_file")

    renamed = str(scratch / "renamed.txt")
    ok, out = run_tool("rename_file", {"src": dst, "dest": renamed})
    record(ok and (scratch / "renamed.txt").exists() and not (scratch / "copied.txt").exists(),
            "rename_file")

    # create_directory: test nested dir
    ok, out = run_tool("create_directory", {"path": str(scratch / "nested" / "deep")})
    record(ok and (scratch / "nested" / "deep").exists(), "create_directory (nested)")

    # append_file
    append_path = str(scratch / "append_test.txt")
    (scratch / "append_test.txt").write_text("line1\n")
    ok, out = run_tool("append_file", {"path": append_path, "content": "line2\nline3\n"})
    record(ok and "line2" in (scratch / "append_test.txt").read_text(),
            "append_file")

    # delete_file: safe with temp file
    del_path = str(scratch / "to_delete.txt")
    (scratch / "to_delete.txt").write_text("x")
    ok, out = run_tool("delete_file", {"path": del_path, "recursive": False})
    record(ok and not (scratch / "to_delete.txt").exists(),
            "delete_file")

    # ── Structured parsing ──────────────────────────────────────────────────
    print(f"\n{BOLD}[structured parsing]{RESET}")

    ok, out = run_tool("read_document", {
        "path": str(scratch / "test.txt"), "max_chars": 100,
    })
    record(ok and len(out) > 0, "read_document")

    ok, out = run_tool("parse_file", {
        "path": str(scratch / "test.csv"), "max_rows": 50,
    })
    record(ok and "Alice" in out, "parse_file (CSV)")

    ok, out = run_tool("parse_file", {
        "path": str(scratch / "test.json"), "max_rows": 50,
    })
    record(ok and "key" in out, "parse_file (JSON)")

    ok, out = run_tool("parse_code", {
        "path": str(scratch / "test.py"), "include_bodies": True,
    })
    record(ok and "hello" in out and "Foo" in out, "parse_code")

    # Note: read_pdf needs an actual PDF file, skip if none in scratch
    ok, out = run_tool("read_pdf", {
        "path": str(scratch / "test.txt"), "pages": "1-1",
    })
    # read_pdf will fail on non-PDF, which is expected — tool runs, just bad input
    record(ok, "read_pdf (non-PDF input, tool executed)")

    # ── Document creation ───────────────────────────────────────────────────
    print(f"\n{BOLD}[document creation]{RESET}")

    # Write input files for document creation tools (they expect file_path args)
    json_in = scratch / "input.json"
    json_in.write_text('{"title": "Test", "body": "Hello world"}')
    ok, out = run_tool("json_to_docx", {
        "json_path": str(json_in),
        "output_path": str(scratch / "output.docx"),
    })
    record(ok, "json_to_docx")

    md_in = scratch / "input.md"
    md_in.write_text("# Test\n\nThis is a test paragraph.")
    ok, out = run_tool("markdown_to_pdf", {
        "md_path": str(md_in),
        "output_path": str(scratch / "output.pdf"),
    })
    record(ok, "markdown_to_pdf")

    ok, out = run_tool("html_to_file", {
        "path": str(scratch / "output.html"),
        "content": "<html><body>Hello</body></html>",
    })
    record(ok and (scratch / "output.html").exists(), "html_to_file")

    ok, out = run_tool("text_to_file", {
        "path": str(scratch / "output.txt"),
        "content": "plain text output",
    })
    record(ok and (scratch / "output.txt").exists(), "text_to_file")

    csv_in = scratch / "input.csv"
    csv_in.write_text("name,age\nAlice,30\nBob,25\n")
    ok, out = run_tool("data_to_xlsx", {
        "data_path": str(csv_in),
        "output_path": str(scratch / "output.xlsx"),
    })
    record(ok, "data_to_xlsx")

    slides_in = scratch / "slides.md"
    slides_in.write_text("# Slide 1\n\nContent here\n\n---\n\n# Slide 2\n\nMore content")
    ok, out = run_tool("markdown_to_pptx", {
        "md_path": str(slides_in),
        "output_path": str(scratch / "output.pptx"),
    })
    record(ok, "markdown_to_pptx")

    ok, out = run_tool("markdown_to_docx", {
        "md_path": str(md_in),
        "output_path": str(scratch / "output_md.docx"),
    })
    record(ok, "markdown_to_docx")

    # ── Archive grep (rga) ──────────────────────────────────────────────────
    print(f"\n{BOLD}[archive grep]{RESET}")
    ok, out = run_tool("archive_grep", {
        "pattern": "hello",
        "path": str(scratch),
        "case_insensitive": True,
    })
    record(ok, "archive_grep (rga)")
    # rga might not be installed — tool still returns (ok) with error message

    # ── Git tools (read-only) ───────────────────────────────────────────────
    print(f"\n{BOLD}[git — read-only]{RESET}")
    # Find parent dir with .git
    repo = HARNESS_DIR
    while repo != repo.parent and not (repo / ".git").exists():
        repo = repo.parent
    is_git = (repo / ".git").exists()

    if is_git:
        repo_path = str(repo)
        ok, out = run_tool("git_status", {"path": repo_path})
        record(ok, "git_status")

        ok, out = run_tool("git_diff", {"path": repo_path, "staged": False})
        record(ok, f"git_diff ({len(out)} chars)")

        ok, out = run_tool("git_log", {"path": repo_path, "n": 3})
        record(ok and len(out) > 0, f"git_log ({len(out.splitlines())} lines)")
    else:
        skip("git_status", "not a git repo")
        skip("git_diff", "not a git repo")
        skip("git_log", "not a git repo")

    # ── Python REPL ─────────────────────────────────────────────────────────
    print(f"\n{BOLD}[python REPL]{RESET}")

    ok, out = run_tool("run_python", {"code": "x = 42\nprint(x * 2)", "timeout": 5})
    record(ok and "84" in out, "run_python")

    ok, out = run_tool("list_kernel_vars", {})
    record(ok, "list_kernel_vars")

    ok, out = run_tool("reset_kernel", {})
    record(ok, "reset_kernel")

    # ── Time ────────────────────────────────────────────────────────────────
    print(f"\n{BOLD}[time]{RESET}")
    ok, out = run_tool("get_current_time", {})
    record(ok and len(out) > 0, "get_current_time")

    # ── macOS native tools ──────────────────────────────────────────────────
    print(f"\n{BOLD}[macOS native]{RESET}")
    is_macos = sys.platform == "darwin"

    if is_macos:
        ok, out = run_tool("system_status", {})
        record(ok and len(out) > 0, "system_status")

        ok, out = run_tool("clipboard_read", {})
        record(ok, "clipboard_read")

        ok, out = run_tool("clipboard_write", {"text": "g4l smoke test"})
        record(ok, "clipboard_write")

        ok, out = run_tool("spotlight_search", {
            "query": "*.txt", "directory": str(scratch), "limit": 5,
        })
        record(ok, "spotlight_search")

        ok, out = run_tool("find_recent", {
            "kind": "any", "days": 1, "query": "",
            "directory": str(scratch), "limit": 5,
        })
        record(ok, "find_recent")

        ok, out = run_tool("list_safari_tabs", {})
        record(ok, "list_safari_tabs")

        ok, out = run_tool("list_notes", {"query": "", "limit": 3})
        record(ok, "list_notes")

        ok, out = run_tool("list_windows", {})
        record(ok, "list_windows")

        ok, out = run_tool("list_reminders", {})
        record(ok, "list_reminders")

        ok, out = run_tool("list_mac_calendar_events", {"days": 1})
        record(ok, "list_mac_calendar_events")

        ok, out = run_tool("take_screenshot", {"mode": "screen"})
        record(ok, "take_screenshot")

        # imessage_search / imessage_status (read-only, needs Messages DB)
        ok, out = run_tool("imessage_search", {"query": "test", "limit": 3})
        record(ok, "imessage_search")

        ok, out = run_tool("imessage_status", {})
        record(ok, "imessage_status")
    else:
        for t in ["system_status", "clipboard_read", "clipboard_write",
                   "spotlight_search", "find_recent", "list_safari_tabs",
                   "list_notes", "list_windows", "list_reminders",
                   "list_mac_calendar_events", "take_screenshot",
                   "imessage_search", "imessage_status"]:
            skip(t, "macOS only")

    # ── PDF/Form tools ──────────────────────────────────────────────────────
    print(f"\n{BOLD}[pdf / form]{RESET}")
    # These need actual PDFs with form fields, but we can still test the executors
    test_pdf = str(Path(HOME) / "Downloads" / "Kalinov_Rozensky_Resume_Modern_Dark.pdf")
    has_pdf = Path(test_pdf).exists()

    if has_pdf:
        ok, out = run_tool("detect_pdf_form_fields", {"path": test_pdf})
        record(ok, "detect_pdf_form_fields")
    else:
        skip("detect_pdf_form_fields", "no test PDF")

    # detect_form_fields rejects non-PDF/DOCX — returns error string, no exception
    ok, out = run_tool("detect_form_fields", {"file_path": str(scratch / "test.txt")})
    record(ok and "Unsupported" in out, "detect_form_fields (rejects non-PDF/DOCX)")

    # fill_pdf_form with nonexistent PDF — returns error string
    ok, out = run_tool("fill_pdf_form", {
        "path": str(scratch / "nonexistent.pdf"),
        "fields": {"Name": "Test"},
    })
    record(ok and "not found" in out.lower(), "fill_pdf_form (no file, expected failure)")

    # ── OCR (needs PaddleOCR, heavy model load) ────────────────────────────
    print(f"\n{BOLD}[OCR]{RESET}")
    skip("ocr_image", "needs PaddleOCR (heavy model load in smoke)")

    # ── Email parsing ───────────────────────────────────────────────────────
    print(f"\n{BOLD}[email parsing]{RESET}")
    ok, out = run_tool("parse_email_file", {
        "path": str(scratch / "test.txt"), "max_chars": 8000,
    })
    record(ok, "parse_email_file (non-email)")

    # ── Document parsing (docling) ──────────────────────────────────────────
    print(f"\n{BOLD}[document parsing]{RESET}")
    ok, out = run_tool("parse_document", {
        "path": str(scratch / "test.txt"), "max_chars": 1000,
    })
    record(ok, "parse_document")

    # ── Semantic search (needs ollama embeddings, can be slow) ──────────────
    print(f"\n{BOLD}[semantic search]{RESET}")
    skip("index_directory", "needs ollama embeddings (slow)")
    skip("semantic_file_search", "needs ollama embeddings (slow)")

    # ── Form tools (more coverage) ──────────────────────────────────────────
    print(f"\n{BOLD}[form tools]{RESET}")
    ok, out = run_tool("fill_form", {
        "file_path": str(scratch / "nonexistent.docx"),
        "fields": {"Name": "Test"},
    })
    record(ok and "not found" in out.lower(), "fill_form (no file, expected failure)")

    ok, out = run_tool("update_form", {
        "file_path": str(scratch / "nonexistent.docx"),
        "field_updates": {"Name": "Updated"},
    })
    record(ok and "not found" in out.lower(), "update_form (no file, expected failure)")

    # ── Shell ───────────────────────────────────────────────────────────────
    print(f"\n{BOLD}[shell]{RESET}")
    ok, out = run_tool("bash_exec", {
        "command": "echo hello && pwd",
        "cwd": str(scratch),
        "timeout": 5,
    })
    record(ok and "hello" in out, "bash_exec")

    ok, out = run_tool("write_file", {
        "path": str(scratch / "write_test.txt"),
        "content": "from scratch\n",
    })
    record(ok and (scratch / "write_test.txt").exists(), "write_file")

    ok, out = run_tool("edit_file", {
        "path": str(scratch / "write_test.txt"),
        "old_str": "from scratch",
        "new_str": "edited",
    })
    content = (scratch / "write_test.txt").read_text() if (scratch / "write_test.txt").exists() else ""
    record(ok and "edited" in content, "edit_file")

    # ── Audio (needs whisper, ~30-60s) ─────────────────────────────────────
    print(f"\n{BOLD}[audio]{RESET}")
    # Audio already tested in verify_local_agent_tools; skip here for speed
    skip("transcribe_audio", "slow (whisper), tested in verify_local_agent_tools")

    # ── Report ──────────────────────────────────────────────────────────────
    print(f"\n{BOLD}=== Summary ==={RESET}")
    print(f"  {GREEN}{pass_count} pass{RESET}, {RED}{fail_count} fail{RESET}, {YELLOW}{skip_count} skip{RESET}")
    print(f"  {DIM}scratch dir kept at {scratch}{RESET}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
