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
from tools.ax_context import _NOT_EDITABLE, find_element, focused_field, settle_title, window_title
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
            "with click_text, using the field's own placeholder text, never a button like 'Search'). Returns what the field reads after typing, or an error if no text field is focused. Set submit=true to press Return afterwards, e.g. to run a search."
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

SCROLL_PAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "scroll_page",
        "description": (
            "Scrolls the page or window the user is looking at, with the orb's hand visibly swiping. Use for 'scroll down', 'scroll up', "
            "'go to the bottom/top of the page', 'scroll right'. direction is where you want to GO: 'down' reveals content further "
            "down. 'top'/'bottom' jump to the ends. pages is how much (1 = about one screenful, default 1; fractions like 0.5 fine). "
            "After scrolling, read the screen with get_screen_context if you need to know what is now visible."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["down", "up", "left", "right", "top", "bottom"], "default": "down"},
                "pages": {"type": "number", "default": 1},
                "app": {"type": "string", "description": "App to bring to the front first, e.g. 'Google Chrome'; omit for the front window."},
            },
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
    try:
        before = window_title(app)
    except Exception:
        before = ""
    _click(*hit)
    where = f"Clicked '{text}' at ({hit[0]:.0f}, {hit[1]:.0f})"
    try:
        after = settle_title(before, app)
    except Exception:
        return where + "."
    if after != before:
        return f"{where}; window changed to '{after}'."
    return f"{where}; window title unchanged (in-page action, or the click may have missed)."


def _type_script(text: str, per: float, submit: bool) -> str:
    esc = text.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'tell application "System Events"\n'
        f'repeat with c in characters of "{esc}"\n'
        f"keystroke c\ndelay {per:.3f}\nend repeat\n"
    )
    if submit:
        script += "key code 36\n"
    return script + "end tell"


def _focus() -> tuple[str, str] | None:
    try:
        return focused_field()
    except Exception:
        return None  # accessibility unreadable: type anyway, report unverified


def type_text(text: str, submit: bool = False) -> str:
    if (why := _guard(typing=True)):
        return why
    f = _focus()
    if f and f[0] in _NOT_EDITABLE:
        # A click that landed on a button or link leaves nothing to type into; saying so beats typing into the void.
        return f"Nothing typed: the focused element is a {f[0][2:].lower()}, not a text field. Click the field itself (use its own placeholder or label text) and retry."
    # One keystroke per character, at the pace the orb's typing hand animates (about 2.5s for any length).
    per = min(0.05, max(0.004, 2.5 / max(len(text), 1)))
    orb_cursor.typing(text, per)
    r = subprocess.run(["osascript", "-e", _type_script(text, per, False)], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return f"Typing failed: {r.stderr.strip()}"
    after = _focus()
    seen = f" Field reads: '{after[1][-60:]}'." if after and after[1] else ""
    verified = bool(after and text[-20:] in after[1])
    if submit:
        subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 36'], capture_output=True, timeout=10)
    note = "" if verified or not after else " (text not visible in the focused field: check it landed)"
    return f"Typed {len(text)} chars" + (" and pressed Return." if submit else ".") + seen + note


_END_PAGES = 80  # 'top'/'bottom': screenfuls to try; the loop stops once the page stops moving
_SCROLL_S = 0.7  # one smooth gesture; long jumps are split into several of these


def scroll_deltas(direction: str, pages: float, page_px: float) -> tuple[float, float, str]:
    """(dx, dy, gesture) in wheel pixels. Wheel convention: positive dy moves content DOWN
    (towards the top of the page), so going down is negative. gesture = the way the content moves."""
    d = (direction or "down").lower()
    pages = _END_PAGES if d in ("top", "bottom") else max(0.1, min(float(pages or 1), 20.0))
    px = page_px * pages
    return {
        "down": (0, -px, "up"), "bottom": (0, -px, "up"),
        "up": (0, px, "down"), "top": (0, px, "down"),
        "right": (-px, 0, "left"), "left": (px, 0, "right"),
    }.get(d, (0, -px, "up"))


def _window_thumb(box) -> list[int]:
    """64x40 grayscale thumbnail of the window, to tell whether a scroll moved the page."""
    from PIL import Image

    x, y, w, h = box
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        subprocess.run(["/usr/sbin/screencapture", "-x", "-R", f"{x:.0f},{y:.0f},{w:.0f},{h:.0f}", path], capture_output=True, timeout=10)
        with Image.open(path) as im:
            return list(im.convert("L").resize((64, 40)).getdata())
    finally:
        os.unlink(path)


def _same_view(a: list[int], b: list[int]) -> bool:
    # A blinking caret or a fading overlay changes a few thumbnail pixels; a scroll changes many.
    return sum(abs(p - q) for p, q in zip(a, b)) / len(a) < 1.5


def scroll_page(direction: str = "down", pages: float = 1, app: str = "") -> str:
    import Quartz

    if app:
        subprocess.run(["/usr/bin/open", "-a", app], capture_output=True, timeout=10)
        time.sleep(0.6)
    if (why := _guard()):
        return why
    front, _ = _front_window()
    box = _front_bounds(front) if front else None
    if box is None:
        return "No front window to scroll."
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    dx, dy, gesture = scroll_deltas(direction, pages, h * 0.85)
    orb_cursor.point(cx, cy, click=False)  # the visible cursor flies to the window first
    time.sleep(_FLY_S)
    # The wheel goes to whatever is under the pointer, so park the real pointer there too.
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, (cx, cy), Quartz.kCGMouseButtonLeft))
    total = max(abs(dx), abs(dy))
    gestures = max(1, int(total // (h * 8)) + 1)  # long jumps (top/bottom) repeat the gesture
    ends = direction in ("top", "bottom")
    last = _window_thumb(box) if ends else None
    moved = not ends
    for g in range(gestures):
        orb_cursor.scrolling(gesture, _SCROLL_S)
        steps = 28
        for i in range(steps):
            # ease-in-out: slow start, fast middle, soft stop, like a trackpad flick
            k0, k1 = i / steps, (i + 1) / steps
            e = lambda k: k * k * (3 - 2 * k)
            sx, sy = round((dx / gestures) * (e(k1) - e(k0))), round((dy / gestures) * (e(k1) - e(k0)))
            ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel, 2, sy, sx)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(_SCROLL_S / steps)
        time.sleep(0.1)
        if ends:
            time.sleep(0.45)  # let the orb's gesture overlay fade, or its animation reads as page movement
            now = _window_thumb(box)
            if _same_view(now, last):
                break  # the page stopped moving: it is at the end
            moved, last = True, now
    if ends and not moved:
        return f"Already at the {direction} of the page (nothing moved)."
    return f"Scrolled {direction}" + (f" {pages:g} page(s)" if direction not in ("top", "bottom") else "") + "."
