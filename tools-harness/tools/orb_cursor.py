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

# Where the bunny currently is. Every payload carries this, because the orb polls the
# file every 80 ms and a gesture written without coordinates left the bunny parked at
# the orb: `point()` wrote {x,y}, then `scrolling()` replaced the whole file with
# {"scroll":…} and the position was gone. A gesture must never erase the anchor.
_LAST_ANCHOR: tuple[float, float] | None = None


def _write(extra: dict, anchor: tuple[float, float] | None = None) -> None:
    """Write one payload. The anchor travels with every payload; the gesture keys do not
    persist (a stale `click: true` must never re-fire on the next event)."""
    global _LAST_ANCHOR
    if anchor is not None:
        _LAST_ANCHOR = anchor
    payload: dict = {"seq": time.time_ns(), "ts": time.time()}
    if _LAST_ANCHOR is not None:
        payload["x"], payload["y"] = _LAST_ANCHOR
        if anchor is None:
            # A gesture that keeps the current anchor has nothing to travel to. Without this
            # the orb flew for 0.75s before every gesture, so a 0.2s read was followed by a
            # late animation at a place the bunny had already left.
            payload["nofly"] = True
    payload.update(extra)
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(_FILE)


def anchor() -> tuple[float, float] | None:
    """The bunny's last known position, or None before it has been anywhere."""
    return _LAST_ANCHOR


# Seconds from the orb receiving a target to the visible click landing. The real click waits this
# long so it happens where the animation says it does. "press" = fly + press; "bazooka" = crouch
# 0.14 + fly 0.75 + draw 0.30 + aim 0.12 + rocket 0.25 (clixen-orb/src/cursor.js and shoot.js;
# tests/test_orb_cursor_style.py keeps the numbers in step).
IMPACT_S = {"press": 0.85, "bazooka": 1.56}


def click_style() -> str:
    """How the bunny clicks. CLIXEN_ORB_CLICK_STYLE=press turns the bazooka off."""
    v = os.environ.get("CLIXEN_ORB_CLICK_STYLE", "bazooka").strip().lower()
    return v if v in IMPACT_S else "bazooka"


def travel_mode() -> str | None:
    """CLIXEN_ORB_TRAVEL=run|hop|skate|rocket pins how the bunny gets there; unset lets the orb pick by distance."""
    v = os.environ.get("CLIXEN_ORB_TRAVEL", "").strip().lower()
    return v if v in ("run", "hop", "skate", "rocket") else None


def point(x: float, y: float, click: bool = True, style: str | None = None) -> None:
    extra: dict = {"click": click}
    if style:
        extra["style"] = style
    if travel := travel_mode():
        extra["travel"] = travel
    _write(extra, anchor=(x, y))


def typing(text: str, per_char_s: float) -> None:
    """Tell the orb to show the typing hand + text bubble at the anchor: it reveals one
    character per `per_char_s`, the same pace the keystrokes are sent at."""
    _write({"type": {"text": text[:400], "per": per_char_s}})


def scrolling(direction: str, duration_s: float) -> None:
    """Two-finger swipe toward `direction` (where the content moves to reveal)."""
    _write({"scroll": {"dir": direction, "dur": duration_s}})


def reading(label: str = "", duration_s: float = 1.2, at=None) -> None:
    """The bunny looks at something: a scan sweep with a magnifier. This is how a *headless*
    tool stays visible — the pixels it reads were on screen, so the reading is shown there."""
    _write({"read": {"label": label[:60], "dur": duration_s}}, anchor=at)


def working(label: str = "", duration_s: float = 2.0, at=None) -> None:
    """Sustained work (a crawl, a download): paws moving with a progress arc, alive for the
    real duration so a 40 s job is 40 s of visible effort, not a frozen sprite."""
    _write({"work": {"label": label[:60], "dur": duration_s}}, anchor=at)


def carrying(label: str = "", at=None) -> None:
    """Something was brought back (a file saved to the workspace): pick up, travel to the
    orb, drop a small chip."""
    _write({"carry": {"label": label[:60]}}, anchor=at)


def thinking(label: str = "", at=None) -> None:
    """No screen work to show (memory, system facts, planning): ears twitch, three dots."""
    _write({"think": {"label": label[:60]}}, anchor=at)


def fail() -> None:
    """The action errored: startled hurt pose at the anchor."""
    _write({"fail": {}})


def dead() -> None:
    """The whole chain was aborted: one dramatic flop, then the cursor fades."""
    _write({"dead": {}})


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
