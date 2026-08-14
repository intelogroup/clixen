"""Generic wrapper around the `gog` CLI (https://github.com/openclaw/gogcli).

One command-line client for Gmail, Calendar, Drive, Docs, Sheets, Tasks, and
the wider Google Workspace surface. Auth is handled entirely by `gog` itself
(OAuth token in macOS Keychain, set up via `gog auth add`) — this wrapper just
shells out with --account/--json pinned and returns stdout/stderr.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess

_DEFAULT_ACCOUNT = os.environ.get("GOG_ACCOUNT", "")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "gog_exec",
        "description": (
            "Run a `gog` CLI command against Google Workspace (Gmail, Calendar, "
            "Drive, Docs, Sheets, Tasks, Contacts, Meet, Chat, Admin). Pass the "
            "subcommand and flags as a single string, e.g. "
            "'tasks list MDI1...' or 'gmail search \"is:unread\" --max 10'. "
            "Do not include the leading 'gog' binary name or --account/--json "
            "flags — those are added automatically. Prefer this over hand-rolled "
            "googleapis scripts for any Workspace read/write."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "The gog subcommand + args, e.g. 'tasks lists', "
                        "'tasks list <tasklistId>', 'gmail search \"newer_than:7d\"'."
                    ),
                },
            },
            "required": ["command"],
        },
    },
}


def _ensure_binary() -> str | None:
    return shutil.which("gog")


def execute(command: str = "") -> str:
    binary = _ensure_binary()
    if not binary:
        return "[gog not installed] Install with: brew install openclaw/tap/gogcli"
    if not command.strip():
        return "[error] command is required, e.g. 'tasks lists'"

    try:
        args = shlex.split(command)
    except ValueError as e:
        return f"[error] could not parse command: {e}"

    cmd = [binary]
    if _DEFAULT_ACCOUNT:
        cmd += ["--account", _DEFAULT_ACCOUNT]
    cmd += args + ["--json"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "[error] gog timed out after 30s"
    except Exception as e:
        return f"[error] gog invocation failed: {e}"

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        if "no tokens stored" in err.lower() or "auth add" in err.lower():
            return f"[gog auth error] {err[:400]} — run `gog auth add <email> --services ...` in a terminal first."
        return f"[gog error rc={result.returncode}] {err[:500]}"

    out = (result.stdout or "").strip()
    return out if out else "(no output)"
