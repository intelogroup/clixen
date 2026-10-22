"""
App lifecycle, Dock, Spaces, system dialogs and system settings for the orb. App/Dock/Space/dialog go
through the Peekaboo CLI (MIT, github.com/openclaw/peekaboo); system uses pmset, system_profiler and
osascript (no per-app Apple Events grant needed). Fence: quit refuses Finder, the orb itself and
core system processes; dialog never types into a password field; nothing here deletes data.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

_MENUBAR_BUTTONS = {"Siri", "Spotlight", "Control Center", "Notification Center", "Clock", "Wi-Fi", "Battery"}
_NEVER_QUIT = {"finder", "clixen orb", "dock", "systemuiserver", "loginwindow", "windowserver"}

APP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "app",
        "description": (
            "Manage running apps. action='list': running apps. 'switch' (name): bring to front. "
            "'hide'/'unhide' (name). 'quit' (name): quit politely (the app still asks about unsaved work); "
            "refuses Finder and system processes. Launching is open_app."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "switch", "hide", "unhide", "quit"]},
                "name": {"type": "string", "description": "App name, e.g. 'Calculator'."},
            },
            "required": ["action"],
        },
    },
}

DOCK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "dock",
        "description": "The Dock. action='list': items and which are running. 'launch' (name): open that Dock item. 'hide'/'show': auto-hide the Dock.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "launch", "hide", "show"]},
                "name": {"type": "string"},
            },
            "required": ["action"],
        },
    },
}

SPACE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "space",
        "description": "Mission Control Spaces. action='list': Spaces and the active one. 'switch' (to): go to Space number `to` (1-based).",
        "parameters": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["list", "switch"]}, "to": {"type": "integer"}},
            "required": ["action"],
        },
    },
}

DIALOG_SCHEMA = {
    "type": "function",
    "function": {
        "name": "dialog",
        "description": (
            "Handle a macOS dialog/alert/sheet on screen. action='list': its buttons and fields. 'click' (button): "
            "press a button by name ('OK', \"Don't Save\", 'Cancel'). 'input' (text, field): type into a field "
            "(never passwords). 'dismiss': close it with Escape."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "click", "input", "dismiss"]},
                "button": {"type": "string"},
                "text": {"type": "string"},
                "field": {"type": "string"},
            },
            "required": ["action"],
        },
    },
}

SYSTEM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "system",
        "description": (
            "Mac system status and settings. action='battery': charge and power source. 'display': screens and "
            "resolution. 'volume': current output volume and mute. 'set_volume' (level 0-100). 'mute'/'unmute'. "
            "'notify' (title, message): show a notification. 'dark_mode' (on: true/false; omit to read)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["battery", "display", "volume", "set_volume", "mute", "unmute", "notify", "dark_mode"]},
                "level": {"type": "integer"},
                "title": {"type": "string"},
                "message": {"type": "string"},
                "on": {"type": "boolean"},
            },
            "required": ["action"],
        },
    },
}


def _peekaboo() -> str | None:
    return shutil.which("peekaboo") or ("/opt/homebrew/bin/peekaboo" if os.path.exists("/opt/homebrew/bin/peekaboo") else None)


def _run(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _pb(args: list[str]) -> subprocess.CompletedProcess | str:
    exe = _peekaboo()
    if not exe:
        return "Error: peekaboo not installed (brew install steipete/tap/peekaboo)."
    try:
        return _run([exe, *args])
    except subprocess.TimeoutExpired:
        return "Error: peekaboo timed out (check Screen Recording/Accessibility for the clixen core process)."


def _fail(r: subprocess.CompletedProcess) -> str:
    return f"Error: {(r.stderr or r.stdout).strip()[:200] or 'peekaboo failed'}"


def _json(r: subprocess.CompletedProcess) -> dict | None:
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def app(action: str, name: str = "") -> str:
    if action not in ("list", "switch", "hide", "unhide", "quit"):
        return f"Unknown app action: {action}."
    if action != "list" and not name.strip():
        return f"app {action} needs a name."
    if action == "quit" and name.strip().lower().removesuffix(".app") in _NEVER_QUIT:
        return f"Refused: '{name}' is Finder, the orb or a system process. Ask the user to do it."
    r = _pb(["app", "list", "--json"] if action == "list" else ["app", "switch", "--to", name] if action == "switch" else ["app", action, "--app", name])
    if isinstance(r, str):
        return r
    if r.returncode != 0:
        return _fail(r)
    if action == "list":
        d = _json(r) or {}
        apps = (d.get("data") or {}).get("apps") or []
        return "\n".join(f"{a.get('name')}{' (frontmost)' if a.get('is_active') else ''}" for a in apps if a.get("name")) or "No apps listed."
    return {"switch": "Switched to", "hide": "Hid", "unhide": "Unhid", "quit": "Quit"}[action] + f" {name}."


def dock(action: str, name: str = "") -> str:
    if action not in ("list", "launch", "hide", "show"):
        return f"Unknown dock action: {action}."
    if action == "launch" and not name.strip():
        return "dock launch needs a name."
    r = _pb(["dock", "list", "--json"] if action == "list" else ["dock", "launch", name] if action == "launch" else ["dock", action])
    if isinstance(r, str):
        return r
    if r.returncode != 0:
        return _fail(r)
    if action == "list":
        items = ((_json(r) or {}).get("data") or {}).get("dockItems") or []
        return "\n".join(f"{i.get('title')}{' (running)' if i.get('running') else ''}" for i in items if i.get("title")) or "Dock is empty."
    return f"Dock {action}: ok." if action != "launch" else f"Launched {name} from the Dock."


def space(action: str, to: int = 0) -> str:
    if action not in ("list", "switch"):
        return f"Unknown space action: {action}."
    if action == "switch" and to < 1:
        return "space switch needs `to` >= 1."
    r = _pb(["space", "list", "--json"] if action == "list" else ["space", "switch", "--to", str(to)])
    if isinstance(r, str):
        return r
    if r.returncode != 0:
        return _fail(r)
    if action == "list":
        sp = ((_json(r) or {}).get("data") or {}).get("spaces") or []
        return "\n".join(f"Space {s.get('id')}{' (active)' if s.get('is_active') else ''}" for s in sp) or "No Spaces listed."
    return f"Switched to Space {to}."


def dialog(action: str, button: str = "", text: str = "", field: str = "") -> str:
    if action not in ("list", "click", "input", "dismiss"):
        return f"Unknown dialog action: {action}."
    if action == "click" and not button.strip():
        return "dialog click needs a button name."
    if action == "input":
        if not text:
            return "dialog input needs text."
        if "password" in field.lower():
            return "Refused: never type into a password field. Ask the user."
    args = {
        "list": ["dialog", "list", "--json"],
        "click": ["dialog", "click", "--button", button],
        "input": ["dialog", "input", "--text", text, *(["--field", field] if field else [])],
        "dismiss": ["dialog", "dismiss"],
    }[action]
    r = _pb(args)
    if isinstance(r, str):
        return r
    if r.returncode != 0:
        return _fail(r)
    if action == "list":
        d = (_json(r) or {}).get("data") or {}
        buttons = {b for b in d.get("buttons", []) if b}
        if not (d.get("title") or d.get("textElements") or d.get("textFields") or buttons - _MENUBAR_BUTTONS):
            return "No dialog on screen."  # Peekaboo falls back to the menu-bar window when none is up
        return json.dumps({k: [x for x in v if x] if isinstance(v, list) else v for k, v in d.items()})[:1500]
    return {"click": f"Clicked '{button}'.", "input": "Typed into the dialog.", "dismiss": "Dismissed the dialog."}[action]


def _osa(script: str) -> str:
    r = _run(["/usr/bin/osascript", "-e", script], timeout=10)
    if r.returncode != 0:
        return f"Error: {r.stderr.strip() or 'osascript failed'}"
    return r.stdout.strip()


def system(action: str, level: int = -1, title: str = "", message: str = "", on: bool | None = None) -> str:
    try:
        if action == "battery":
            return _run(["/usr/bin/pmset", "-g", "batt"]).stdout.strip()
        if action == "display":
            d = json.loads(_run(["/usr/sbin/system_profiler", "SPDisplaysDataType", "-json"], timeout=15).stdout)
            rows = [f"{x.get('_name')}: {x.get('_spdisplays_resolution') or x.get('spdisplays_resolution', '?')}"
                    for g in d.get("SPDisplaysDataType", []) for x in g.get("spdisplays_ndrvs", [])]
            return "\n".join(rows) or "No displays reported."
        if action == "volume":
            return _osa('set v to get volume settings\nreturn "volume " & (output volume of v) & ", muted " & (output muted of v)')
        if action == "set_volume":
            if not 0 <= level <= 100:
                return "set_volume needs level 0-100."
            was_muted = _osa("output muted of (get volume settings)") == "true"
            err = _osa(f"set volume output volume {level}")  # macOS unmutes as a side effect
            if was_muted and not err:
                err = _osa("set volume output muted true")
            return err or f"Volume set to {level}."
        if action in ("mute", "unmute"):
            return _osa(f"set volume output muted {'true' if action == 'mute' else 'false'}") or f"{action.capitalize()}d."
        if action == "notify":
            if not message.strip():
                return "notify needs a message."
            r = _run(["/usr/bin/osascript", "-e", "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run", title or "Clixen", message])
            return "Notification shown." if r.returncode == 0 else f"Error: {r.stderr.strip()}"
        if action == "dark_mode":
            if on is None:
                return "dark mode " + _osa('tell application "System Events" to tell appearance preferences to return dark mode')
            return _osa(f'tell application "System Events" to tell appearance preferences to set dark mode to {"true" if on else "false"}') or f"Dark mode {'on' if on else 'off'}."
    except subprocess.TimeoutExpired:
        return f"Error: {action} timed out."
    except ValueError as e:
        return f"Error: could not parse {action} output: {e}"
    return f"Unknown system action: {action}."
