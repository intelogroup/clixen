"""
Ambient screen watch — lets the orb answer "what am I looking at" without a
screenshot + vision-model round trip.

Poller thread (wired into core.py) captures the front-most window every few
seconds, skips frames that look unchanged, OCRs changed ones locally (Apple
Vision via a tiny compiled swift helper) and writes a rolling text digest to
disk. `get_screen_context` just reads that digest. Nothing leaves the machine
and no frame is ever written anywhere but a temp file deleted right after OCR.

Fence: OFF by default (flag file, toggled by `set_screen_watch`). Skips the
lock screen, password managers and private/incognito windows. Front-most window
only, main display. The vision model is still the tool for charts/images.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

_log = logging.getLogger(__name__)

_DATA_DIR = Path.home() / ".config" / "g4l" / "data"
_STATE_FILE = _DATA_DIR / "screen_watch_state.json"
_FLAG_FILE = _DATA_DIR / "screen_watch_enabled"
_BIN_DIR = _DATA_DIR / "bin"
_OCR_BIN = _BIN_DIR / "screen_ocr"
_OCR_SRC = Path(__file__).with_name("screen_ocr.swift")

_HISTORY_TTL_S = 600  # keep the last 10 min of digests
_HISTORY_MAX = 20
_TEXT_MAX = 4000
_STALE_S = 30
_CHANGE_THRESHOLD = 4.0  # mean abs gray diff (0-255) on a 32x32 thumbnail

# Never watch these, regardless of the flag.
_BLOCKED_APPS = ("1password", "keychain access", "bitwarden", "lastpass", "dashlane", "passwords")
_BLOCKED_TITLE_WORDS = ("private browsing", "incognito", "inprivate")

GET_SCREEN_CONTEXT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_screen_context",
        "description": (
            "See what is on the user's Mac screen right now, from an always-on local watcher: "
            "front-most app, window title, and the visible text (OCR), plus a short history of "
            "what was on screen in the last few minutes. Instant, no screenshot taken. Use for "
            "'what am I looking at', 'what was I just doing', 'explain this' about on-screen "
            "content. For charts, images or UI layout where text isn't enough, use ask_vision_agent. "
            "Returns a message if the watcher is off (user must enable it via set_screen_watch)."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

SET_SCREEN_WATCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_screen_watch",
        "description": (
            "Turn the ambient screen watcher on or off. Only call when the user explicitly asks "
            "to start/stop watching their screen. Off deletes the stored digest."
        ),
        "parameters": {
            "type": "object",
            "properties": {"on": {"type": "boolean", "description": "true = watch, false = stop"}},
            "required": ["on"],
        },
    },
}


def is_enabled() -> bool:
    return _FLAG_FILE.exists()


def set_screen_watch(on: bool) -> str:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if on:
        _FLAG_FILE.touch()
        return "Screen watch ON. I'll keep a local text digest of your front-most window."
    _FLAG_FILE.unlink(missing_ok=True)
    _STATE_FILE.unlink(missing_ok=True)
    return "Screen watch OFF. Stored digest deleted."


def is_blocked(app: str, title: str) -> bool:
    a, t = app.lower(), title.lower()
    return any(b in a for b in _BLOCKED_APPS) or any(w in t for w in _BLOCKED_TITLE_WORDS)


def frame_changed(prev: list[int] | None, cur: list[int]) -> bool:
    """prev/cur: flat 32x32 grayscale thumbnails."""
    if prev is None or len(prev) != len(cur):
        return True
    return sum(abs(a - b) for a, b in zip(prev, cur)) / len(cur) >= _CHANGE_THRESHOLD


def add_history(history: list[dict], entry: dict, now: float) -> list[dict]:
    """Append; collapse a repeat of the same app+title; prune by age and count."""
    if history and (history[-1]["app"], history[-1]["title"]) == (entry["app"], entry["title"]):
        history = history[:-1]
    history = [h for h in history + [entry] if now - h["ts"] < _HISTORY_TTL_S]
    return history[-_HISTORY_MAX:]


def _osascript(script: str) -> str:
    try:
        return subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return ""


def _front_window() -> tuple[str, str]:
    app = _osascript(
        'tell application "System Events" to get name of first process whose frontmost is true'
    )
    title = _osascript(
        'tell application "System Events" to tell (first process whose frontmost is true) '
        "to get name of window 1"
    )
    return app, title


def _screen_locked() -> bool:
    try:
        import Quartz

        d = Quartz.CGSessionCopyCurrentDictionary() or {}
        return bool(d.get("CGSSessionScreenIsLocked", False))
    except Exception:
        return False


def _front_window_id(app: str) -> int | None:
    try:
        import Quartz

        for w in Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, 0
        ):
            if str(w.get("kCGWindowOwnerName", "")).lower() == app.lower() and w.get("kCGWindowLayer") == 0:
                return int(w["kCGWindowNumber"])
    except Exception:
        pass
    return None


def _capture(path: str, window_id: int | None) -> bool:
    cmd = ["/usr/sbin/screencapture", "-x", "-t", "jpg"]
    if window_id:
        cmd += ["-o", f"-l{window_id}"]
    r = subprocess.run(cmd + [path], capture_output=True, timeout=10)
    return r.returncode == 0 and os.path.getsize(path) > 0


def _thumb(path: str) -> list[int]:
    from PIL import Image

    with Image.open(path) as im:
        return list(im.convert("L").resize((32, 32)).getdata())


def _ensure_ocr_bin() -> Path | None:
    if _OCR_BIN.exists() and _OCR_BIN.stat().st_mtime >= _OCR_SRC.stat().st_mtime:
        return _OCR_BIN
    _BIN_DIR.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["swiftc", "-O", str(_OCR_SRC), "-o", str(_OCR_BIN)], capture_output=True, timeout=180
    )
    return _OCR_BIN if r.returncode == 0 else None


def _ocr(path: str) -> str:
    binary = _ensure_ocr_bin()
    if binary is None:
        return ""
    try:
        r = subprocess.run([str(binary), path], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return ""
    return r.stdout.strip()[:_TEXT_MAX] if r.returncode == 0 else ""


def _write_state(state: dict) -> None:
    tmp = _STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(_STATE_FILE)


def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {}


def _tick(prev_thumb: list[int] | None, prev_key: tuple[str, str] | None):
    """One poll. Returns (thumb, key) to carry over; writes state when it changes."""
    now = time.time()
    state = _load_state()
    if _screen_locked():
        return None, None
    app, title = _front_window()
    if not app or is_blocked(app, title):
        # Drop any stale view of a window we must not retain.
        if state:
            state.update(app="", title="", text="", updated_at=now)
            _write_state(state)
        return None, None

    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        if not _capture(path, _front_window_id(app)):
            return prev_thumb, prev_key
        thumb = _thumb(path)
        key = (app, title)
        if key == prev_key and not frame_changed(prev_thumb, thumb):
            state["updated_at"] = now  # still alive, nothing new
            _write_state(state)
            return prev_thumb, prev_key
        text = _ocr(path)
    finally:
        os.unlink(path)

    history = add_history(
        state.get("history", []), {"ts": now, "app": app, "title": title, "text": text[:600]}, now
    )
    _write_state({"app": app, "title": title, "text": text, "updated_at": now, "history": history})
    return thumb, key


def run_screen_watch_daemon(poll_seconds: float = 3.0):
    """Background loop — call from a daemon thread (see core.py)."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    thumb, key = None, None
    while True:
        try:
            if is_enabled():
                thumb, key = _tick(thumb, key)
            else:
                thumb, key = None, None
        except Exception:
            _log.exception("screen_watch tick failed")
        time.sleep(poll_seconds)


def get_screen_context() -> str:
    if not is_enabled():
        return "Screen watch is off. Ask the user if they want it on, then call set_screen_watch(on=true)."
    state = _load_state()
    if not state:
        return "Screen watch is on but nothing captured yet — retry in a few seconds."
    if time.time() - state.get("updated_at", 0) > _STALE_S:
        return "Screen watch has no fresh capture (screen locked, blocked app, or watcher stalled)."
    if not state.get("app"):
        return "Front-most window is private/blocked; not captured."

    now = time.time()
    recent = [
        f"- {int(now - h['ts'])}s ago: {h['app']} — {h['title']}"
        for h in state.get("history", [])[:-1]
    ]
    return (
        f"Front-most: {state['app']} — {state['title']}\n\n{state.get('text', '')}"
        + ("\n\nEarlier:\n" + "\n".join(recent) if recent else "")
    )
