"""
Deterministic window and menu-bar control for the orb via System Events (AX), so "move Chrome to
the left half" or "File > New Tab" never depends on keystroke guessing. Every value reaches
AppleScript as argv, never as script text. Fence: window close is the red button only (never quits the app); menu clicks refuse close/quit/delete
items and disabled items.
"""
from __future__ import annotations

import re
import subprocess
import time

WINDOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "window",
        "description": (
            "Arrange the user's own app windows. action='list' (app): title, position, size of each "
            "window. 'focus' (app, index): raise it. 'move' (app, x, y) and 'resize' (app, width, height): "
            "screen points, origin top-left. 'tile' (app, index, position): fill a named region - use it for "
            "'put X on the left', never move+resize arithmetic. 'minimize' (app, index), 'zoom' (fill the screen's usable area, not full screen), 'fullscreen' (toggle full screen), "
            "'close' (red button: the app still asks about unsaved work; never quits the app). index is 1-based, "
            "default 1. Use for 'put X on the left', 'make this bigger', 'bring Y forward', 'close this window'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "focus", "move", "resize", "tile", "minimize", "zoom", "fullscreen", "close"]},
                "app": {"type": "string", "description": "Process name, e.g. 'Google Chrome', 'Finder'."},
                "index": {"type": "integer", "description": "1-based window index, default 1."},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "position": {
                    "type": "string",
                    "enum": ["left", "right", "top", "bottom", "top-left", "top-right", "bottom-left", "bottom-right", "maximize", "center"],
                    "description": "tile only: which half or quarter of the usable screen to fill.",
                },
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
    "close": '''on run argv
tell application "System Events" to tell process (item 1 of argv)
click (first button of window (item 2 of argv as integer) whose subrole is "AXCloseButton")
return "Closed window " & (item 2 of argv)
end tell
end run''',
    "fullscreen": '''on run argv
tell application "System Events" to tell process (item 1 of argv)
set w to window (item 2 of argv as integer)
set now to value of attribute "AXFullScreen" of w
set value of attribute "AXFullScreen" of w to (not now)
return "Full screen " & (not now)
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


_VISIBLE_FRAME_JXA = (
    "ObjC.import('AppKit');"
    "const f=$.NSScreen.mainScreen.visibleFrame, h=$.NSScreen.screens.objectAtIndex(0).frame.size.height;"
    "[Math.round(f.origin.x), Math.round(h-f.origin.y-f.size.height), Math.round(f.size.width), Math.round(f.size.height)].join(',')"
)


def _usable_area() -> tuple[int, int, int, int] | None:
    """x, y (top-left origin), width, height of the main screen minus menu bar and Dock."""
    try:
        r = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", _VISIBLE_FRAME_JXA], capture_output=True, text=True, timeout=10)
        x, y, w, h = (int(v) for v in r.stdout.strip().split(","))
        return x, y, w, h
    except (subprocess.TimeoutExpired, ValueError):
        return None


_BOUNDS_JXA = (
    'ObjC.import("AppKit");'
    'function run(argv){'
    '  const app = Application("System Events").processes.byName(argv[0]);'
    '  const w = app.windows[Number(argv[1]) - 1];'
    '  const p = w.position(), z = w.size();'
    '  return [p[0], p[1], z[0], z[1]].join(",");'
    '}'
)


def _read_bounds(app: str, index: int) -> tuple[int, int, int, int] | None:
    """Where the window actually is, a moment after being told to move."""
    try:
        time.sleep(0.25)
        r = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", _BOUNDS_JXA, str(app), str(index)],
            capture_output=True, text=True, timeout=10,
        )
        x, y, w, h = (int(float(v)) for v in r.stdout.strip().split(","))
        return x, y, w, h
    except (subprocess.TimeoutExpired, ValueError):
        return None


def _tile_rect(area: tuple[int, int, int, int], position: str) -> tuple[int, int, int, int] | None:
    """x, y, w, h for a named screen region, from the usable area.

    Window placement is arithmetic the model gets wrong: "put Chrome on the left"
    used to mean `move` + `resize` with hand-computed numbers, and a wrong number
    silently puts the window half off-screen. One call, exact halves/quarters.
    """
    x, y, w, h = area
    half_w, half_h = w // 2, h // 2
    rects = {
        "left": (x, y, half_w, h),
        "right": (x + half_w, y, w - half_w, h),
        "top": (x, y, w, half_h),
        "bottom": (x, y + half_h, w, h - half_h),
        "top-left": (x, y, half_w, half_h),
        "top-right": (x + half_w, y, w - half_w, half_h),
        "bottom-left": (x, y + half_h, half_w, h - half_h),
        "bottom-right": (x + half_w, y + half_h, w - half_w, h - half_h),
        "maximize": (x, y, w, h),
        "center": (x + (w - int(w * 0.7)) // 2, y + (h - int(h * 0.7)) // 2, int(w * 0.7), int(h * 0.7)),
    }
    return rects.get(position.strip().lower().replace("_", "-"))


def window(action: str, app: str, index: int = 1, x: int | None = None, y: int | None = None,
           width: int | None = None, height: int | None = None,
           position: str = "") -> str:
    if action in ("tile", "zoom"):
        if not app.strip() or index < 1:
            return "window needs an app name and a 1-based index."
        area = _usable_area()
        if area is None:
            return "Error: could not read the screen size."
        rect = (area[0], area[1], area[2], area[3]) if action == "zoom" else _tile_rect(area, position)
        if rect is None:
            return (
                f"Unknown position '{position}'. Use one of: left, right, top, bottom, "
                "top-left, top-right, bottom-left, bottom-right, maximize, center."
            )
        tx, ty, tw, th = rect
        err = _run(_WINDOW["move"], app, str(index), str(tx), str(ty))
        if err.startswith("Error"):
            return err
        out = _run(_WINDOW["resize"], app, str(index), str(tw), str(th))
        if out.startswith("Error"):
            return out
        label = action if action == "zoom" else f"tile {position}"
        # Read back instead of trusting the request: macOS may clamp a window to a
        # minimum size or nudge it on-screen, and a bare "tiled to 735x861" would then
        # be a claim the screen does not support. Measured: an immediate read can still
        # show the previous frame, so settle first.
        actual = _read_bounds(app, index)
        if actual and (actual[0], actual[1]) != (tx, ty):
            return (
                f"Tiled window {index} of {app} ({label}); asked for {tw}x{th} at "
                f"({tx},{ty}), the window is at ({actual[0]},{actual[1]}) {actual[2]}x{actual[3]} — "
                "macOS adjusts some windows, so this is where it actually is."
            )
        return f"Tiled window {index} of {app}: {label} -> {tw}x{th} at ({tx},{ty})."
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
