"""
Tell the Clixen Orb where an action "clicked", so its cursor can fly there.

Cross-process by design: tools run in core.py, the orb is a separate Tauri app,
so the target goes through a small state file the orb polls (same pattern as
preview_watch). The orb ignores anything older than a few seconds, so a stale
file never replays a gesture. Coordinates are logical screen points, top-left
origin — what Quartz window bounds and Tauri positions both use.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_FILE = Path.home() / ".config" / "g4l" / "data" / "orb_cursor.json"


# Seconds from the orb receiving a target to the visible click landing. The real click waits this
# long so it happens where the animation says it does. "press" = fly + press; "bazooka" = crouch
# 0.14 + fly 0.75 + draw 0.30 + aim 0.12 + rocket 0.25 (clixen-orb/src/cursor.js and shoot.js;
# tests/test_orb_cursor_style.py keeps the numbers in step).
IMPACT_S = {"press": 0.85, "bazooka": 1.56}


def click_style() -> str:
    """How the bunny clicks. CLIXEN_ORB_CLICK_STYLE=press turns the bazooka off."""
    v = os.environ.get("CLIXEN_ORB_CLICK_STYLE", "bazooka").strip().lower()
    return v if v in IMPACT_S else "bazooka"


def point(x: float, y: float, click: bool = True, style: str | None = None) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    payload = {"seq": time.time_ns(), "ts": time.time(), "x": x, "y": y, "click": click}
    if style:
        payload["style"] = style
    tmp.write_text(json.dumps(payload))
    tmp.replace(_FILE)


def typing(text: str, per_char_s: float) -> None:
    """Tell the orb to show the typing hand + text bubble at the parked cursor: it reveals one
    character per `per_char_s`, the same pace the keystrokes are sent at."""
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seq": time.time_ns(), "ts": time.time(), "type": {"text": text[:400], "per": per_char_s}}))
    tmp.replace(_FILE)


def scrolling(direction: str, duration_s: float) -> None:
    """Tell the orb to show the two-finger scroll gesture at the parked cursor, swiping toward
    `direction` (up/down/left/right: where the content moves to reveal) for `duration_s`."""
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seq": time.time_ns(), "ts": time.time(), "scroll": {"dir": direction, "dur": duration_s}}))
    tmp.replace(_FILE)


def front_window_center(app_name: str, wait_s: float = 0.6) -> tuple[float, float] | None:
    """Centre of `app_name`'s front-most on-screen window, once it has had a
    moment to appear. None if it has no window (the orb then just doesn't fly)."""
    time.sleep(wait_s)
    try:
        import Quartz

        for w in Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, 0
        ):
            b = w["kCGWindowBounds"]
            if (
                str(w.get("kCGWindowOwnerName", "")).lower() == app_name.lower()
                and w.get("kCGWindowLayer") == 0
                and b["Width"] > 200
                and b["Height"] > 200
            ):
                return b["X"] + b["Width"] / 2, b["Y"] + b["Height"] / 2
    except Exception:
        pass
    return None


def point_at_app(app_name: str) -> None:
    c = front_window_center(app_name)
    if c:
        point(*c)
