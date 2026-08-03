"""Native macOS surface tools: clipboard, Safari tabs, Notes, AppleScript runner.

All run via `osascript` / `pbpaste` / `pbcopy` — no PyObjC dependency.
First call to any AppleScript-driven tool may trigger a one-time Automation
permission prompt (System Settings → Privacy → Automation).
"""
from __future__ import annotations

import shutil
import subprocess


_TIMEOUT_S = 10


def _osa(script: str) -> tuple[int, str, str]:
    """Run an AppleScript and return (rc, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", "osascript timed out"
    except OSError as e:
        return 1, "", str(e)


# ─── Clipboard ─────────────────────────────────────────────────────────────

CLIPBOARD_READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "clipboard_read",
        "description": (
            "Read the current macOS clipboard (text). Use for 'summarize what I just copied' "
            "or 'paste my clipboard into the email'."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

CLIPBOARD_WRITE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "clipboard_write",
        "description": "Replace the macOS clipboard with the given text. Useful as the last step before 'now paste it where I want it'.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to put on the clipboard."},
            },
            "required": ["text"],
        },
    },
}


def clipboard_read() -> str:
    if shutil.which("pbpaste") is None:
        return "[clipboard error] pbpaste not found (macOS only)."
    try:
        out = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=_TIMEOUT_S
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"[clipboard error] {e}"
    text = out.stdout or ""
    if not text:
        return "[clipboard] empty."
    if len(text) > 8000:
        return text[:8000] + f"\n... [truncated; {len(text) - 8000} more chars]"
    return text


def clipboard_write(text: str) -> str:
    if shutil.which("pbcopy") is None:
        return "[clipboard error] pbcopy not found (macOS only)."
    if text is None:
        return "[clipboard error] text required."
    try:
        proc = subprocess.run(
            ["pbcopy"], input=text, text=True, timeout=_TIMEOUT_S
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"[clipboard error] {e}"
    if proc.returncode != 0:
        return f"[clipboard error] pbcopy exited {proc.returncode}"
    return f"[clipboard] wrote {len(text)} chars."


# ─── Safari tabs ───────────────────────────────────────────────────────────

SAFARI_TABS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_safari_tabs",
        "description": (
            "List the URLs and titles of every open tab in Safari. "
            "Use to answer 'what was that article I had open?' or to bulk-extract reading list. "
            "First call may prompt for Automation permission."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def list_safari_tabs() -> str:
    script = (
        'set out to ""\n'
        'tell application "Safari"\n'
        '  if not running then return "[safari] not running."\n'
        '  repeat with w in windows\n'
        '    repeat with t in tabs of w\n'
        '      set out to out & (URL of t) & " | " & (name of t) & linefeed\n'
        '    end repeat\n'
        '  end repeat\n'
        'end tell\n'
        'return out'
    )
    rc, stdout, stderr = _osa(script)
    if rc != 0:
        if "not authorized" in stderr.lower() or "1743" in stderr:
            return "[safari error] Automation permission denied. Grant in System Settings → Privacy & Security → Automation."
        return f"[safari error] {stderr or 'unknown'}"
    # AppleScript may return our own "[safari] not running." sentinel — pass through as-is.
    if stdout.startswith("[safari]"):
        return stdout
    if not stdout:
        return "[safari] no open tabs."
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    return f"{len(lines)} Safari tab(s):\n" + "\n".join(f"  {ln}" for ln in lines)


# ─── Notes ─────────────────────────────────────────────────────────────────

NOTES_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_notes",
        "description": (
            "Search the user's Apple Notes by title or body content. "
            "Use to answer 'do I have a note about X' or 'find my note titled Y'. "
            "Returns folder, title, and modification date for each match. "
            "First call may prompt for Automation permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Substring to match against note name or body.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max notes to return (1–25). Default 10.",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
}


def list_notes(query: str, limit: int = 10) -> str:
    q = (query or "").strip()
    if not q:
        return "[notes error] empty query."
    cap = max(1, min(int(limit or 10), 25))
    # Escape double quotes in the query for AppleScript embedding.
    q_safe = q.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'set q to "{q_safe}"\n'
        f'set maxN to {cap}\n'
        'set out to ""\n'
        'set hits to 0\n'
        'tell application "Notes"\n'
        '  repeat with n in notes\n'
        '    if hits ≥ maxN then exit repeat\n'
        '    try\n'
        '      set t to name of n\n'
        '      set b to plaintext of n\n'
        '      if (t contains q) or (b contains q) then\n'
        '        set f to "(no folder)"\n'
        '        try\n'
        '          set f to name of (container of n)\n'
        '        end try\n'
        '        set md to modification date of n\n'
        '        set out to out & f & " | " & t & " | " & (md as string) & linefeed\n'
        '        set hits to hits + 1\n'
        '      end if\n'
        '    end try\n'
        '  end repeat\n'
        'end tell\n'
        'return out'
    )
    rc, stdout, stderr = _osa(script)
    if rc != 0:
        if "not authorized" in stderr.lower() or "1743" in stderr:
            return "[notes error] Automation permission denied. Grant in System Settings → Privacy & Security → Automation."
        return f"[notes error] {stderr or 'unknown'}"
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return f"No Notes matching '{q}'."
    return f"{len(lines)} Notes match(es) for '{q}':\n" + "\n".join(f"  {ln}" for ln in lines)


# ─── Notes — create ────────────────────────────────────────────────────────

NOTES_CREATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "notes_create",
        "description": (
            "Create a new note in Apple Notes with a title and body. "
            "Use for 'jot down…', 'save this as a note', 'make me a note about X'. "
            "Body supports plain text — no Markdown rendering inside Notes.app. "
            "First call may prompt for Automation permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title (becomes the first line)."},
                "body": {"type": "string", "description": "Note body. Plain text.", "default": ""},
                "folder": {
                    "type": "string",
                    "description": "Optional Notes folder (e.g. 'Notes', 'Quick Notes'). Default folder when blank.",
                    "default": "",
                },
                "account": {
                    "type": "string",
                    "description": "Notes account: 'iCloud' or 'On My Mac'. Default 'iCloud'.",
                    "default": "iCloud",
                },
            },
            "required": ["title"],
        },
    },
}


def notes_create(title: str, body: str = "", folder: str = "", account: str = "iCloud") -> str:
    title = (title or "").strip()
    if not title:
        return "[notes_create error] title required."
    body = body or ""
    account = (account or "iCloud").strip()

    # Build a body that displays nicely: title becomes H1, body follows.
    # Notes.app interprets newlines but not real markdown — we keep it plain.
    title_safe = title.replace("\\", "\\\\").replace('"', '\\"')
    # AppleScript needs CR for newlines inside string literals; we substitute.
    body_lines = body.replace("\r\n", "\n").split("\n")
    body_as = " & return & ".join(
        f'"{ln.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}"'
        for ln in body_lines
    ) if body else '""'

    if folder:
        folder_safe = folder.replace("\\", "\\\\").replace('"', '\\"')
        target_block = (
            f'    tell account "{account}"\n'
            f'      tell folder "{folder_safe}"\n'
            '        set newNote to make new note with properties {name:noteTitle, body:noteBody}\n'
            '      end tell\n'
            '    end tell\n'
        )
    else:
        target_block = (
            f'    tell account "{account}"\n'
            '      set newNote to make new note with properties {name:noteTitle, body:noteBody}\n'
            '    end tell\n'
        )

    script = (
        f'set noteTitle to "{title_safe}"\n'
        f'set noteBody to {body_as}\n'
        'tell application "Notes"\n'
        f'{target_block}'
        '  return name of newNote\n'
        'end tell'
    )
    rc, stdout, stderr = _osa(script)
    if rc != 0:
        if "not authorized" in stderr.lower() or "1743" in stderr:
            return "[notes_create error] Automation permission denied. Grant in System Settings → Privacy & Security → Automation."
        if "Can't get folder" in stderr or "Can’t get folder" in stderr:
            return f"[notes_create error] folder {folder!r} not found in account {account!r}."
        if "Can't get account" in stderr or "Can’t get account" in stderr:
            return f"[notes_create error] account {account!r} not found. Try 'iCloud' or 'On My Mac'."
        return f"[notes_create error] {stderr or 'unknown'}"
    return f"Created note: {stdout or title}"


# ─── Generic AppleScript escape hatch ─────────────────────────────────────

APPLESCRIPT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "applescript_run",
        "description": (
            "Run an arbitrary AppleScript snippet via osascript. ADVANCED — use only when "
            "a more specific tool doesn't exist. Output is the script's return value."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "AppleScript source."},
            },
            "required": ["script"],
        },
    },
}


def applescript_run(script: str) -> str:
    if not script or not script.strip():
        return "[applescript error] empty script."
    rc, stdout, stderr = _osa(script)
    if rc != 0:
        return f"[applescript error] rc={rc} {stderr or ''}".strip()
    return stdout or "[applescript] (no output)"
