"""
Element-ID targeting for the orb via the Peekaboo CLI (MIT, github.com/openclaw/peekaboo): ui_see
returns a compact list of clickable elements with stable IDs, ui_click presses one by ID. Use it
where click_text is ambiguous (icon buttons, duplicate labels). Fence: ui_click only accepts an ID
that the last ui_see for that app returned, and refuses close/quit/delete/trash controls.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

UI_SEE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ui_see",
        "description": (
            "List the clickable controls of an app's front window as 'id role label' lines, using the "
            "accessibility tree. Call it before ui_click. Use for icon-only buttons or when click_text "
            "found several matches. Does not click."
        ),
        "parameters": {
            "type": "object",
            "properties": {"app": {"type": "string", "description": "App name, e.g. 'Finder'. Omit for the frontmost app."}},
        },
    },
}

UI_CLICK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ui_click",
        "description": (
            "Click a control by the id that ui_see returned for the same app (e.g. 'elem_173'). "
            "Refuses ids not from the latest ui_see, and close/quit/delete/trash controls."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "app": {"type": "string", "description": "Same app you passed to ui_see; omit if you omitted it there."},
                "double": {"type": "boolean"},
                "right": {"type": "boolean"},
            },
            "required": ["id"],
        },
    },
}

UI_DRAG_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ui_drag",
        "description": "Drag one control onto another by the ids ui_see returned for the same app (move a file onto a folder, reorder a list). Refuses ids not from the latest ui_see.",
        "parameters": {
            "type": "object",
            "properties": {"from_id": {"type": "string"}, "to_id": {"type": "string"}, "app": {"type": "string"}},
            "required": ["from_id", "to_id"],
        },
    },
}

_MAX_ELEMENTS = 80
_REFUSED = ("close", "quit", "delete", "trash", "remove", "erase", "log out", "shut down", "restart", "sleep")
# app key ("" = frontmost) -> {element id: label}, from the latest ui_see
_last_seen: dict[str, dict[str, str]] = {}
# same key -> {element id: (x, y)} screen-point center from the snapshot frame
_last_center: dict[str, dict[str, tuple[int, int]]] = {}


def _peekaboo() -> str | None:
    return shutil.which("peekaboo") or ("/opt/homebrew/bin/peekaboo" if os.path.exists("/opt/homebrew/bin/peekaboo") else None)


def _run(args: list[str], timeout: int = 25) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _centers(snapshot_id: str) -> dict[str, tuple[int, int]]:
    """Element-id -> frame center, read from Peekaboo's snapshot file (the see JSON carries no frames)."""
    try:
        with open(os.path.expanduser(f"~/.peekaboo/snapshots/{snapshot_id}/snapshot.json")) as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return {}
    out: dict[str, tuple[int, int]] = {}
    stack = [snap]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            fr = o.get("frame")
            if o.get("id") and isinstance(fr, list) and len(fr) == 2 and fr[1][0] > 0 and fr[1][1] > 0:
                out[o["id"]] = (int(fr[0][0] + fr[1][0] / 2), int(fr[0][1] + fr[1][1] / 2))
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return out


def _label(e: dict) -> str:
    return (e.get("label") or e.get("title") or e.get("description") or "").strip()


def ui_see(app: str = "") -> str:
    exe = _peekaboo()
    if not exe:
        return "Error: peekaboo not installed (brew install steipete/tap/peekaboo)."
    try:
        r = _run([exe, "see", "--app", app or "frontmost", "--json"])
    except subprocess.TimeoutExpired:
        return "Error: peekaboo see timed out (check Screen Recording/Accessibility for the clixen core process)."
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return f"Error: {(r.stderr or r.stdout).strip()[:200] or 'peekaboo see failed'}"
    if not d.get("success"):
        return f"Error: {str(d.get('error') or d)[:200]}"
    data = d["data"]
    seen: dict[str, str] = {}
    lines = []
    for e in data.get("ui_elements", []):
        label = _label(e)
        if not e.get("is_actionable") or not label or label in ("image", "group"):
            continue
        if e["id"] in seen:
            continue
        seen[e["id"]] = label
        lines.append(f"{e['id']} {e['role']} {label}")
        if len(lines) >= _MAX_ELEMENTS:
            break
    _last_seen[app] = seen
    centers = _centers(data.get("snapshot_id", ""))
    _last_center[app] = {i: centers[i] for i in seen if i in centers}
    head = f"{data.get('application_name')} - {data.get('window_title')}: {len(lines)} of {data.get('interactable_count')} controls"
    return head + "\n" + "\n".join(lines) if lines else head + " (none labelled)"


def ui_click(id: str, app: str = "", double: bool = False, right: bool = False) -> str:
    label = _last_seen.get(app, {}).get(id)
    if label is None:
        return f"Refused: {id} was not returned by the latest ui_see{' for ' + app if app else ''}. Call ui_see first."
    if any(w in label.lower() for w in _REFUSED):
        return f"Refused: '{label}' closes, quits or deletes something. Ask the user to do it."
    exe = _peekaboo()
    if not exe:
        return "Error: peekaboo not installed."
    center = _last_center.get(app, {}).get(id)
    if center is None:
        return f"Error: no on-screen position recorded for {id}. Call ui_see again."
    # --coords, not --on: Peekaboo's own element click lands a sidebar row too high in Finder (beta4).
    args = [exe, "click", "--coords", f"{center[0]},{center[1]}", *(["--app", app] if app else []), *(["--double"] if double else []), *(["--right"] if right else [])]
    try:
        r = _run(args)
    except subprocess.TimeoutExpired:
        return "Error: peekaboo click timed out."
    if r.returncode != 0:
        return f"Error: {(r.stderr or r.stdout).strip()[:200] or 'peekaboo click failed'}"
    return f"Clicked {id} ({label})."


def ui_drag(from_id: str, to_id: str, app: str = "") -> str:
    centers = _last_center.get(app, {})
    for i in (from_id, to_id):
        if i not in centers:
            return f"Refused: {i} was not returned by the latest ui_see{' for ' + app if app else ''}. Call ui_see first."
    exe = _peekaboo()
    if not exe:
        return "Error: peekaboo not installed."
    (fx, fy), (tx, ty) = centers[from_id], centers[to_id]
    try:
        r = _run([exe, "drag", "--from-coords", f"{fx},{fy}", "--to-coords", f"{tx},{ty}", *(["--app", app] if app else [])])
    except subprocess.TimeoutExpired:
        return "Error: peekaboo drag timed out."
    if r.returncode != 0:
        return f"Error: {(r.stderr or r.stdout).strip()[:200] or 'peekaboo drag failed'}"
    labels = _last_seen.get(app, {})
    return f"Dragged {from_id} ({labels.get(from_id, '?')}) onto {to_id} ({labels.get(to_id, '?')})."
