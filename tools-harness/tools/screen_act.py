"""
Click / type on the user's real screen, with the orb's cursor flying to the
target first so the user watches the action happen.

Targeting is by visible text: full-screen capture -> Apple Vision OCR with boxes
(same swift helper as screen_watch) -> pick the best line match -> fly the orb
cursor there -> real CGEvent click when it lands. No DOM, so it works in the
user's own Chrome tab or any app.

Fence: main display only; refuses the lock screen and screen_watch's blocked
apps / private windows; types only into whatever has focus after a click.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time

from tools import orb_cursor
from tools.ax_context import find_element
from tools.screen_watch import _ensure_ocr_bin, _front_window, _screen_locked, is_blocked

CLICK_TEXT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "click_text",
        "description": (
            "The tool for every 'click X' / 'click on X' / 'press X' / 'select X' request (e.g. 'click Bletchley Park', 'click on Surviving machines'): clicks something on the user's real screen by its visible text (a link, button, tab, "
            "menu item, search box label). The orb's cursor visibly flies to it and clicks. Works "
            "in the user's own Chrome and any other app, no DOM needed. Pass the text as it "
            "appears on screen. Never ask the user where: it searches whatever is visible on screen. Returns what was clicked, or that the text was not found. Use "
            "for ANY 'click X' about the page or app the user is looking at (even a website in their Chrome); prefer it over ask_browser_agent, which uses a separate browser. Use it after open_url/open_app when the task needs a click; use type_text to enter text "
            "after clicking a field."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Visible text to click."},
                "app": {"type": "string", "description": "App to bring to the front first, e.g. 'Google Chrome'. Pass it whenever the user names an app or site's browser; omit to act on whatever is already in front."},
            },
            "required": ["text"],
        },
    },
}

TYPE_TEXT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "type_text",
        "description": (
            "Only for entering text characters (never for clicking: use click_text for that). Types text into whatever field currently has focus on the user's screen (click it first "
            "with click_text). Set submit=true to press Return afterwards, e.g. to run a search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "submit": {"type": "boolean", "default": False},
            },
            "required": ["text"],
        },
    },
}

_FLY_S = 0.85  # orb cursor: fly out 0.75s, then it presses


# Typing + Return into a shell would run it; never do that unasked.
_TERMINALS = ("terminal", "iterm", "ghostty", "warp", "alacritty", "kitty", "wezterm")


def _guard(typing: bool = False) -> str | None:
    if _screen_locked():
        return "Screen is locked."
    app, title = _front_window()
    if app and is_blocked(app, title):
        return "Front window is private or a password manager; not acting on it."
    if typing and any(t in app.lower() for t in _TERMINALS):
        return f"Front app is a terminal ({app}); not typing into it."
    return None


def _front_bounds(app: str) -> tuple[float, float, float, float] | None:
    """x, y, w, h (points) of `app`'s front-most normal window."""
    import Quartz

    for w in Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, 0
    ):
        bd = w["kCGWindowBounds"]
        if str(w.get("kCGWindowOwnerName", "")).lower() == app.lower() and w.get("kCGWindowLayer") == 0 and bd["Width"] > 200:
            return bd["X"], bd["Y"], bd["Width"], bd["Height"]
    return None


def _ocr_boxes(accurate: bool = False) -> list[tuple[str, float, float, float, float]]:
    """OCR lines of the front-most window (so text echoed in other windows, e.g. a terminal
    behind it, never matches), boxes in screen points, top-left origin. Falls back to the
    whole main display when the front app has no window."""
    import Quartz

    binary = _ensure_ocr_bin()
    if binary is None:
        return []
    b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    ox, oy, sw, sh = 0.0, 0.0, b.size.width, b.size.height
    app, _ = _front_window()
    region = _front_bounds(app) if app else None
    cmd = ["/usr/sbin/screencapture", "-x"]
    if region:
        ox, oy, sw, sh = region
        cmd += ["-R", f"{ox:.0f},{oy:.0f},{sw:.0f},{sh:.0f}"]
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        subprocess.run(cmd + [path], capture_output=True, timeout=10)
        flags = ["--boxes", *(["--accurate"] if accurate else [])]
        r = subprocess.run([str(binary), path, *flags], capture_output=True, text=True, timeout=40 if accurate else 20)
    finally:
        os.unlink(path)
    lines = []
    for row in r.stdout.splitlines():
        parts = row.split("\t")
        if len(parts) == 5:
            t, x, y, w, h = parts[0], *map(float, parts[1:])
            lines.append((t, ox + x * sw, oy + y * sh, w * sw, h * sh))
    return lines


def find_text(lines, want: str):
    """Best line for `want`: exact > whole-word-ish substring > substring; shorter line wins ties.
    Returns (cx, cy) in the line's own text span when the match is a substring."""
    w = want.strip().lower()
    best = None
    for t, x, y, bw, bh in lines:
        tl = t.lower()
        if w not in tl:
            continue
        score = (0 if tl.strip() == w else 1, len(tl))
        if best is None or score < best[0]:
            # centre of the matched span, assuming even glyph width across the line
            i = tl.index(w)
            frac = (i + len(w) / 2) / max(len(tl), 1)
            best = (score, (x + bw * frac, y + bh / 2))
    return best[1] if best else None


def _click(x: float, y: float) -> None:
    import Quartz

    orb_cursor.point(x, y, click=True)
    time.sleep(_FLY_S)  # let the visible cursor land first
    pt = (x, y)
    for ev in (Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        e = Quartz.CGEventCreateMouseEvent(None, ev, pt, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        time.sleep(0.05)


def click_text(text: str, app: str = "") -> str:
    if app:
        subprocess.run(["/usr/bin/open", "-a", app], capture_output=True, timeout=10)
        time.sleep(0.6)
    if (why := _guard()):
        return why
    try:
        hit = find_element(text)  # native accessibility position first: exact, no OCR
    except Exception:
        hit = None
    hit = hit or find_text(_ocr_boxes(), text) or find_text(_ocr_boxes(accurate=True), text)
    if hit is None:
        return f"Could not find '{text}' on screen."
    _click(*hit)
    return f"Clicked '{text}' at ({hit[0]:.0f}, {hit[1]:.0f})."


def type_text(text: str, submit: bool = False) -> str:
    if (why := _guard(typing=True)):
        return why
    # AppleScript keystroke handles unicode and layout; System Events is already needed elsewhere.
    esc = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "System Events" to keystroke "{esc}"'
    if submit:
        script += '\ntell application "System Events" to key code 36'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return f"Typing failed: {r.stderr.strip()}"
    return f"Typed {len(text)} chars" + (" and pressed Return." if submit else ".")
