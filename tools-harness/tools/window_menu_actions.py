"""
Deterministic window and menu-bar control for the orb via System Events (AX), so "move Chrome to
the left half" or "File > New Tab" never depends on keystroke guessing. Every value reaches
AppleScript as argv, never as script text. Fence: no close/quit/delete; menu clicks only on items
that are currently enabled.
"""
from __future__ import annotations

import re
import subprocess

WINDOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "window",
        "description": (
            "Arrange the user's own app windows. action='list' (app): title, position, size of each "
            "window. 'focus' (app, index): raise it. 'move' (app, x, y) and 'resize' (app, width, height): "
            "screen points, origin top-left. 'minimize' (app, index). index is 1-based, default 1. "
            "Use for 'put X on the left', 'make this window bigger', 'bring Y forward'. Never closes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "focus", "move", "resize", "minimize"]},
                "app": {"type": "string", "description": "Process name, e.g. 'Google Chrome', 'Finder'."},
                "index": {"type": "integer", "description": "1-based window index, default 1."},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
            },
            "required": ["action", "app"],
        },
    },
}

MENU_SCHEMA = {
    "type": "function",
    "function": {
        "name": "menu",
        "description": (
            "Use an app's menu bar. action='list' (app, path): items of a menu, path like 'File' or "
            "'File > Open Recent'; disabled items are marked. action='click' (app, path): press an "
            "enabled item, path like 'File > New Tab' or 'View > Zoom In'. Prefer this over press_keys "
            "when you don't know the shortcut. Cannot press disabled items."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "click"]},
                "app": {"type": "string", "description": "Process name, e.g. 'Google Chrome'."},
                "path": {"type": "string", "description": "Menu path separated by ' > '."},
            },
            "required": ["action", "app", "path"],
        },
    },
}

# argv: app, index, then per-action values
_WINDOW = {
    "list": '''on run argv
tell application "System Events" to tell process (item 1 of argv)
set out to {}
repeat with i from 1 to count of windows
set w to window i
set p to position of w
set z to size of w
set end of out to (i as text) & ". " & (name of w as text) & " @" & ((item 1 of p) as text) & "," & ((item 2 of p) as text) & " " & ((item 1 of z) as text) & "x" & ((item 2 of z) as text)
end repeat
set AppleScript's text item delimiters to linefeed
return out as text
end tell
end run''',
    "focus": '''on run argv
tell application "System Events" to tell process (item 1 of argv)
set frontmost to true
perform action "AXRaise" of window (item 2 of argv as integer)
return "Focused window " & (item 2 of argv)
end tell
end run''',
    "move": '''on run argv
tell application "System Events" to tell process (item 1 of argv)
set position of window (item 2 of argv as integer) to {(item 3 of argv as integer), (item 4 of argv as integer)}
return "Moved to " & (item 3 of argv) & "," & (item 4 of argv)
end tell
end run''',
    "resize": '''on run argv
tell application "System Events" to tell process (item 1 of argv)
set size of window (item 2 of argv as integer) to {(item 3 of argv as integer), (item 4 of argv as integer)}
return "Resized to " & (item 3 of argv) & "x" & (item 4 of argv)
end tell
end run''',
    "minimize": '''on run argv
tell application "System Events" to tell process (item 1 of argv)
set value of attribute "AXMinimized" of window (item 2 of argv as integer) to true
return "Minimized window " & (item 2 of argv)
end tell
end run''',
}

# argv: app, then the menu path parts (top-level menu first)
_MENU_NAV = '''set cur to menu bar item (item 2 of argv) of menu bar 1
repeat with i from 3 to count of argv
set cur to menu item (item i of argv) of menu 1 of cur
end repeat'''
_MENU = {
    "list": f'''on run argv
tell application "System Events" to tell process (item 1 of argv)
{_MENU_NAV}
set out to {{}}
repeat with m in (menu items of menu 1 of cur)
set n to name of m
if n is missing value then
set n to "-"
else if not (enabled of m) then
set n to n & " (disabled)"
end if
set end of out to n
end repeat
set AppleScript's text item delimiters to linefeed
return out as text
end tell
end run''',
    "click": f'''on run argv
tell application "System Events" to tell process (item 1 of argv)
set frontmost to true
{_MENU_NAV}
if not (enabled of cur) then error "menu item is disabled"
click cur
return "Clicked " & (item (count of argv) of argv)
end tell
end run''',
}


_REFUSED_ITEMS = ("quit", "close", "delete", "move to trash", "empty trash", "force quit", "log out",
                  "shut down", "restart", "erase", "sleep")


def _osascript(script: str, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/usr/bin/osascript", "-e", script, *argv], capture_output=True, text=True, timeout=10)


def _run(script: str, *argv: str) -> str:
    try:
        r = _osascript(script, *argv)
    except subprocess.TimeoutExpired:
        return "Error: System Events did not respond (check Accessibility/Automation for the clixen core process)."
    if r.returncode != 0:
        err = r.stderr.strip()
        if re.search(r"Can[\u2019']t (get|set) process \"", err):
            return f"Error: app not running: {err}"
        return f"Error: {err or 'osascript failed'}"
    return r.stdout.strip()


def window(action: str, app: str, index: int = 1, x: int | None = None, y: int | None = None,
           width: int | None = None, height: int | None = None) -> str:
    script = _WINDOW.get(action)
    if script is None:
        return f"Unknown window action: {action}."
    if not app.strip():
        return "window needs an app name."
    if index < 1:
        return "index is 1-based."
    argv = [app, str(index)]
    if action == "move":
        if x is None or y is None:
            return "move needs x and y."
        argv += [str(x), str(y)]
    elif action == "resize":
        if not width or not height or width < 1 or height < 1:
            return "resize needs positive width and height."
        argv += [str(width), str(height)]
    out = _run(script, *argv)
    return out or ("No windows." if action == "list" else out)


def menu(action: str, app: str, path: str) -> str:
    script = _MENU.get(action)
    if script is None:
        return f"Unknown menu action: {action}."
    parts = [p.strip() for p in path.split(">") if p.strip()]
    if not app.strip() or not parts:
        return "menu needs an app and a path."
    if action == "click" and len(parts) < 2:
        return "click needs a full path like 'File > New Tab'."
    if action == "click" and parts[-1].lower().startswith(_REFUSED_ITEMS):
        return f"Refused: '{parts[-1]}' closes, quits or deletes something. Ask the user to do it."
    return _run(script, app, *parts)
