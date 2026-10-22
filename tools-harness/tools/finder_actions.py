"""
Direct Finder control for the orb: open a folder, reveal a file, read the selection or the
front window's folder. Deterministic AppleScript, never keystrokes (the Go-to-Folder sheet is
unreliable to drive with press_keys/type_text). Paths go in via argv, never into script text.
Fence: existing paths only; no delete/move/rename.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

FINDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "finder",
        "description": (
            "Control the user's own Finder. action='open_folder' (path): show that folder in a Finder "
            "window ('open my Downloads'). action='reveal' (path): select a file/folder in Finder. "
            "action='selection': list what is selected in Finder now. action='front_path': folder shown "
            "in the front Finder window. Use this instead of press_keys/type_text for any Finder "
            "navigation. Never moves, renames or deletes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["open_folder", "reveal", "selection", "front_path"]},
                "path": {"type": "string", "description": "Absolute or ~ path. Needed for open_folder and reveal."},
            },
            "required": ["action"],
        },
    },
}

_SCRIPTS = {
    "open_folder": "",
    "reveal": "",
    "selection": '''tell application "Finder"
set out to {}
repeat with i in (get selection)
set end of out to POSIX path of (i as alias)
end repeat
set AppleScript's text item delimiters to linefeed
return out as text
end tell''',
    "front_path": '''tell application "Finder"
if (count of Finder windows) = 0 then return ""
return POSIX path of (target of front Finder window as alias)
end tell''',
}


def _osascript(script: str, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/usr/bin/osascript", "-e", script, *argv], capture_output=True, text=True, timeout=15)


def finder(action: str, path: str = "") -> str:
    script = _SCRIPTS.get(action)
    if script is None:
        return f"Unknown finder action: {action}."
    argv: tuple[str, ...] = ()
    if action in ("open_folder", "reveal"):
        if not path.strip():
            return f"finder {action} needs a path."
        p = Path(path).expanduser()
        if not p.exists():
            return f"Path not found: {path}."
        if action == "open_folder" and not p.is_dir():
            return f"Not a folder: {path}. Use reveal for a file."
        argv = (str(p.resolve()),)
    if action in ("open_folder", "reveal"):
        # `open` needs no Apple Events grant; osascript->Finder hangs under launchd until the
        # core process is granted Automation > Finder.
        args = ["/usr/bin/open", *(["-R"] if action == "reveal" else []), argv[0]]
        r = subprocess.run(args, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return f"Error: {r.stderr.strip() or 'open failed'}"
        return f"{'Opened' if action == 'open_folder' else 'Revealed'} {argv[0]}"
    try:
        r = _osascript(script, *argv)
    except subprocess.TimeoutExpired:
        return "Error: Finder did not respond (grant Automation > Finder to the clixen core process)."
    if r.returncode != 0:
        return f"Error: {r.stderr.strip() or 'osascript failed'}"
    out = r.stdout.strip()
    if action == "selection":
        return out or "Nothing selected in Finder."
    if action == "front_path":
        return out or "No Finder window open."
    return out
