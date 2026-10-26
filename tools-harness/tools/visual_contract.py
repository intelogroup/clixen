"""Every orb tool call must be *visible*.

The orb is a visual agent, not a headless browser: it may use headless machinery
(`scrapling_api_capture`, `workspace_download`, `read_page_text`) but the user must
always be able to see the bunny doing something in the viewport. Before this module
existed, 4 of 51 tools animated and everything else was silent — a six-call research
task showed the bunny sitting still for twenty seconds.

Two rules, enforced by tests rather than remembered:

1. Every tool in `orb_agent.TOOL_NAMES` appears in `GESTURES`. A new tool without an
   entry fails the suite, so silence is a build error instead of a habit.
2. A gesture never mimes something that did not happen: a failure resolves to the hurt
   pose at the *same* anchor, never a success flourish.

Anchors come from the tool's own arguments where possible, because "show me where this
happened" is the whole point:

    element   the target the tool was given
    browser   the browser window the facts came from — used for headless reads so the
              user sees provenance, not just motion
    front     whatever window is in front
    orb       nothing on screen to point at (memory, system facts) — think at the orb
"""
from __future__ import annotations

import time

from tools import orb_cursor

# tool -> (gesture, anchor)
GESTURES: dict[str, tuple[str, str]] = {
    # --- acts on the screen: the tool emits click/type/scroll with the real target ---
    "act": ("click", "element"),
    "click_text": ("click", "element"),
    "type_text": ("type", "element"),
    "scroll_page": ("scroll", "front"),
    # --- looks at the screen: reads stay visible even when the reader is headless ---
    "observe": ("read", "front"),
    "get_screen_context": ("read", "front"),
    "read_page_text": ("read", "browser"),
    "page_probe": ("read", "browser"),
    "ocr_image": ("read", "front"),
    "take_screenshot": ("read", "front"),
    "analyze_image": ("read", "front"),
    "find_on_page": ("read", "front"),
    "wait_for": ("read", "front"),
    # --- headless web work, shown at the browser window it came from ---
    "scrapling_fetch_and_extract": ("read", "browser"),
    "scrapling_extract": ("read", "browser"),
    "scrapling_api_capture": ("read", "browser"),
    "scrapling_crawl": ("work", "browser"),
    "ask_web_search": ("read", "browser"),
    "ask_fetch_url": ("read", "browser"),
    "wikipedia_references": ("read", "browser"),
    "app_task": ("work", "browser"),
    "open_url": ("click", "browser"),
    "tabs": ("click", "browser"),
    # --- brought something back: shown as carrying it to the orb ---
    "workspace_download": ("carry", "orb"),
    "workspace_write": ("carry", "orb"),
    "workspace_delete": ("carry", "orb"),
    "workspace_list": ("read", "orb"),
    "workspace_read": ("read", "orb"),
    "read_file": ("read", "orb"),
    "clipboard_write": ("carry", "orb"),
    "clipboard_read": ("read", "orb"),
    # --- window / app control: the bunny goes to the window it changes ---
    "app": ("click", "front"),
    "open_app": ("click", "front"),
    "window": ("click", "front"),
    "menu": ("click", "front"),
    "dialog": ("click", "front"),
    "finder": ("click", "front"),
    "system": ("click", "front"),
    # --- nothing on screen to point at: think at the orb ---
    "remember": ("think", "orb"),
    "update_task_plan": ("think", "orb"),
    "get_current_time": ("think", "orb"),
    "system_status": ("think", "orb"),
    "get_latest_email": ("think", "orb"),
    "list_emails": ("think", "orb"),
    "read_email": ("think", "orb"),
    "list_calendar_events": ("think", "orb"),
    "create_calendar_event": ("think", "orb"),
    "list_tasks": ("think", "orb"),
    "complete_task": ("think", "orb"),
    "list_reminders": ("think", "orb"),
    "load_skill": ("think", "orb"),
    "tool_help": ("think", "orb"),
}

_BROWSERS = ("google chrome", "safari", "firefox", "brave browser", "microsoft edge", "chromium")

# A window-centre lookup is a Quartz walk; a five-call task resolves the same browser
# window five times inside a second, so cache it briefly.
_CACHE: dict[str, tuple[float, tuple[float, float] | None]] = {}
_CACHE_S = 2.0



def _cached_center(app_name: str) -> tuple[float, float] | None:
    now = time.time()
    hit = _CACHE.get(app_name)
    if hit and now - hit[0] < _CACHE_S:
        return hit[1]
    center = orb_cursor.front_window_center(app_name, wait_s=0)
    _CACHE[app_name] = (now, center)
    return center


def _window_center(app_name: str) -> tuple[float, float] | None:
    """Centre of `app_name`'s front window, falling back to the frontmost app that has one."""
    if app_name:
        if c := _cached_center(app_name):
            return c
    for name in ("Google Chrome", "Safari", "Finder", "Ghostty", "Terminal"):
        if c := _cached_center(name):
            return c
    return None


def _browser_center(args: dict) -> tuple[float, float] | None:
    app = str(args.get("app") or "").strip()
    if app and app.lower() in _BROWSERS:
        return _window_center(app)
    for name in ("Google Chrome", "Safari", "Firefox", "Brave Browser"):
        if c := _cached_center(name):
            return c
    return None


def resolve(kind: str, args: dict) -> tuple[float, float] | None:
    """Screen point for an anchor kind, or None when there is nothing to point at."""
    if kind == "orb":
        return None
    if kind == "element":
        try:
            return float(args["x"]), float(args["y"])
        except (KeyError, TypeError, ValueError):
            # The tool resolves its own target from an element id; this anchor only decides
            # where the bunny waits, so the window centre is the honest default.
            return _browser_center(args) or _window_center(str(args.get("app") or ""))
    if kind == "browser":
        return _browser_center(args)
    if kind == "front":
        return _window_center(str(args.get("app") or ""))
    return None


def announce(tool: str, args: dict | None = None) -> tuple[str, str] | None:
    """Tell the orb what this call is doing, before it runs. Returns (gesture, anchor).

    Only a gesture with somewhere to happen moves the cursor first; a `think` stays at the
    orb, because teleporting the bunny to a window for "what time is it" would be a lie
    about where the work happened.
    """
    spec = GESTURES.get(tool)
    if not spec:
        return None
    gesture, kind = spec
    args = args or {}
    label = tool.replace("_", " ")
    try:
        where = resolve(kind, args)
        # Already there? Keep the anchor and skip the flight (see orb_cursor._write).
        prev = orb_cursor.anchor()
        if where and prev and abs(prev[0] - where[0]) < 40 and abs(prev[1] - where[1]) < 40:
            where = None
        # One payload per call: the anchor and the gesture travel together, because writing
        # `point()` and then the gesture is a race against the orb's 80 ms poll — the fly
        # gets overwritten before it is read, and the gesture plays at the old position.
        if gesture == "read":
            orb_cursor.reading(label, at=where)
        elif gesture == "work":
            orb_cursor.working(label, at=where)
        elif gesture == "carry":
            orb_cursor.carrying(label, at=where)
        elif gesture == "think":
            orb_cursor.thinking(label, at=where)
        # click / type / scroll are emitted by the tool itself, against the real target.
    except Exception:
        pass  # an animation must never be able to fail the tool the user asked for
    return gesture, kind


def settle(tool: str, ok: bool) -> None:
    """A failure is shown where it happened — never a success flourish on a lie."""
    if ok:
        return
    try:
        orb_cursor.fail()
    except Exception:
        pass


def gestures_for(tools) -> dict[str, tuple[str, str]]:
    """The subset of GESTURES covering `tools` (used by the coverage test)."""
    return {name: GESTURES[name] for name in tools if name in GESTURES}
