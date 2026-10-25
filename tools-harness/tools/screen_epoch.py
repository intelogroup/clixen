"""Which app the model last *looked* at, so input tools can refuse to act blind.

`act()` is safe against a moving target: it takes an element id from observe and
rejects a stale one, so it can never hit the wrong control. `click_text()` and
`type_text()` have no such anchor — they work by label or by keystroke on
whatever is frontmost at the moment they run. Look at Chrome, switch to
Terminal, then type: the keystrokes land in the terminal.

This module records the app/title from the last screen read. The input tools
compare the current front window against it and refuse when it changed, which
turns a silent mis-type into "look again".

Deliberately small and dependency-free: it is written from screen_v2.observe()
and read from screen_act._guard(), two modules that otherwise only share a
package.
"""
from __future__ import annotations

import time

# How long a look stays authoritative. Long enough for observe -> act -> type,
# short enough that a stale snapshot cannot vouch for a screen the user has
# since repurposed.
LOOK_TTL_S = 120.0

_last: dict[str, object] = {"app": "", "title": "", "at": 0.0}


def note_look(app: str, title: str = "") -> None:
    """Record that the model just saw `app` in front."""
    if app:
        _last.update(app=str(app), title=str(title or ""), at=time.monotonic())


def described() -> str:
    """'Chrome ("Inbox")' for a message, or '' when nothing was recorded."""
    app = str(_last.get("app") or "")
    if not app:
        return ""
    title = str(_last.get("title") or "")
    return f'{app} ("{title[:60]}")' if title else app


def changed_since_look(front_app: str, front_title: str = "", ttl: float = LOOK_TTL_S) -> str | None:
    """Return an explanation when the front app is not the one last looked at.

    None means "safe to act": either nothing was recorded (no worse than before
    this guard existed), the record is older than `ttl`, or the app is the same.
    """
    app = str(_last.get("app") or "")
    at = float(_last.get("at") or 0.0)
    if not app or not front_app or (time.monotonic() - at) > ttl:
        return None
    if front_app.strip().lower() == app.strip().lower():
        return None
    when = max(1, int(time.monotonic() - at))
    described_now = self_describe(front_app, front_title)
    return (
        f"Nothing done: you last looked at {described()} {when}s ago and {described_now} is in front now. "
        "The screen moved under you, so a label or a keystroke would land in the wrong place. "
        "Look again (observe, or get_screen_context) and retry."
    )


def self_describe(app: str, title: str = "") -> str:
    return f'{app} ("{title[:60]}")' if title else app


def reset() -> None:
    """Test hook: forget the last look."""
    _last.update(app="", title="", at=0.0)
