"""
Native window context via the macOS Accessibility API (AXUIElement): which app and
windows are up, and the real on-screen position of every link / button / field in
the front window. No screenshot, no OCR, so it is exact on small blue link text
and cheap enough to call at any time. OCR (screen_act) stays as the fallback for
apps that expose no accessibility tree (canvas apps, games, some Electron views).

Positions are logical screen points, top-left origin, same as Quartz bounds and the
orb cursor. Fence: read-only; refuses the lock screen and screen_watch's blocked
apps / private windows.
"""
from __future__ import annotations

import time

from tools.screen_watch import _screen_locked, is_blocked

GET_WINDOW_CONTEXT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_window_context",
        "description": (
            "Native, exact view of the user's Mac windows, any time: front app, its windows, the "
            "focused window's title and the clickable elements in it (links, buttons, fields, "
            "tabs, menu items) with their on-screen label and x,y centre. Uses the Accessibility "
            "API, not OCR, so it is instant and precise, including inside the user's own Chrome. "
            "Use for 'what window/app am I in', 'what can I click here', 'where is the X button'. "
            "Pass `query` to list only elements whose label contains it. Pass `app` to inspect a "
            "specific app instead of the front one. For reading page text use get_screen_context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Only elements whose label contains this."},
                "app": {"type": "string", "description": "App name; default is the front-most app."},
            },
            "required": [],
        },
    },
}

_ACTIONABLE = {
    "AXLink", "AXButton", "AXTextField", "AXTextArea", "AXSearchField", "AXCheckBox", "AXRadioButton",
    "AXPopUpButton", "AXMenuItem", "AXMenuBarItem", "AXTab", "AXComboBox", "AXDisclosureTriangle",
    "AXMenuButton", "AXSlider",
}
_MAX_NODES = 25000
_DEADLINE_S = 4.0


def _get(el, attr):
    from ApplicationServices import AXUIElementCopyAttributeValue

    err, val = AXUIElementCopyAttributeValue(el, attr, None)
    return val if err == 0 else None


def _point(v) -> tuple[float, float] | None:
    from ApplicationServices import AXValueGetValue, kAXValueCGPointType

    ok, p = AXValueGetValue(v, kAXValueCGPointType, None) if v is not None else (False, None)
    return (p.x, p.y) if ok else None


def _size(v) -> tuple[float, float] | None:
    from ApplicationServices import AXValueGetValue, kAXValueCGSizeType

    ok, s = AXValueGetValue(v, kAXValueCGSizeType, None) if v is not None else (False, None)
    return (s.width, s.height) if ok else None


def _label(el) -> str:
    for attr in ("AXTitle", "AXDescription", "AXValue", "AXHelp"):
        v = _get(el, attr)
        if isinstance(v, str) and v.strip():
            return " ".join(v.split())
    return ""


def _target_app(name: str = ""):
    """(pid, localized name) of the named app, else the front-most one."""
    from AppKit import NSWorkspace

    ws = NSWorkspace.sharedWorkspace()
    if name:
        apps = [a for a in ws.runningApplications() if (a.localizedName() or "").lower() == name.lower()]
        apps.sort(key=lambda a: not a.isActive())  # the active instance first (two Chromes)
        a = apps[0] if apps else None
    else:
        a = ws.frontmostApplication()
    return (a.processIdentifier(), a.localizedName() or "") if a else (None, "")


def _ax_app(pid: int):
    from ApplicationServices import AXUIElementCreateApplication, AXUIElementSetAttributeValue

    el = AXUIElementCreateApplication(pid)
    # Chromium/Electron build their web tree only once an assistive client asks (the set
    # reports an error but still flips it on); the tree appears within about a second.
    AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
    return el


def _window_rect(win) -> tuple[float, float, float, float] | None:
    p, s = _point(_get(win, "AXPosition")), _size(_get(win, "AXSize"))
    return (p[0], p[1], s[0], s[1]) if p and s else None


def elements(app: str = "", query: str = "") -> tuple[str, str, list[dict]]:
    """(app name, window title, on-screen actionable elements) of the target app's focused
    window. Each element: {role, label, x, y, w, h} with x,y the centre."""
    pid, name = _target_app(app)
    if pid is None:
        return "", "", []
    app_el = _ax_app(pid)
    win = _get(app_el, "AXFocusedWindow") or _get(app_el, "AXMainWindow")
    if win is None:
        return name, "", []
    rect = _window_rect(win)
    title = _get(win, "AXTitle") or ""
    q = query.lower().strip()

    out: list[dict] = []
    seen: set[tuple] = set()
    deadline = time.monotonic() + _DEADLINE_S
    stack = [win]
    n = 0
    # Chromium may still be building its tree on the very first call: retry once if the walk is tiny.
    for attempt in range(2):
        while stack and n < _MAX_NODES and time.monotonic() < deadline:
            el = stack.pop()
            n += 1
            role = _get(el, "AXRole")
            if role in _ACTIONABLE:
                p, s = _point(_get(el, "AXPosition")), _size(_get(el, "AXSize"))
                if p and s and s[0] > 0 and s[1] > 0:
                    cx, cy = p[0] + s[0] / 2, p[1] + s[1] / 2
                    inside = rect is None or (rect[0] <= cx <= rect[0] + rect[2] and rect[1] <= cy <= rect[1] + rect[3])
                    label = _label(el)
                    key = (role, label, round(cx), round(cy))
                    if inside and key not in seen and (label or role in ("AXTextField", "AXTextArea", "AXSearchField")) and (not q or q in label.lower()):
                        seen.add(key)
                        out.append({"role": role[2:], "label": label, "x": round(cx), "y": round(cy), "w": round(s[0]), "h": round(s[1])})
            kids = _get(el, "AXChildren")
            if kids:
                stack.extend(reversed(kids))  # keep document order
        if n > 200 or attempt:
            break
        time.sleep(1.2)
        stack, n = [win], 0
    return name, title, out


def window_list() -> list[str]:
    """One line per normal on-screen window, front to back."""
    import Quartz

    rows = []
    for w in Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, 0
    ):
        b = w["kCGWindowBounds"]
        if w.get("kCGWindowLayer") == 0 and b["Width"] > 100 and b["Height"] > 100:
            rows.append(f"{w.get('kCGWindowOwnerName', '?')} — {w.get('kCGWindowName', '') or '(untitled)'} "
                        f"[{b['X']:.0f},{b['Y']:.0f} {b['Width']:.0f}x{b['Height']:.0f}]")
    return rows


def find_element(label: str, app: str = "") -> tuple[float, float] | None:
    """Centre of the best on-screen element whose label matches: exact > shortest substring."""
    _, _, els = elements(app, label)
    w = label.strip().lower()
    best = None
    for e in els:
        lab = e["label"].lower()
        score = (0 if lab == w else 1, len(lab))
        if best is None or score < best[0]:
            best = (score, (float(e["x"]), float(e["y"])))
    return best[1] if best else None


def get_window_context(query: str = "", app: str = "") -> str:
    if _screen_locked():
        return "Screen is locked."
    name, title, els = elements(app, query)
    if name and is_blocked(name, title):
        return "Front window is private or a password manager; not reading it."
    lines = [f"Front app: {name} — {title or '(no focused window)'}", "Windows:"]
    lines += [f"- {w}" for w in window_list()[:12]]
    if not els:
        lines.append("Elements: none exposed (app has no accessibility tree; try get_screen_context or click_text OCR).")
    else:
        lines.append(f"Elements ({len(els)}, x,y = centre in screen points):")
        lines += [f"- {e['role']} '{e['label'][:60]}' ({e['x']},{e['y']}) {e['w']}x{e['h']}" for e in els[:80]]
        if len(els) > 80:
            lines.append(f"… {len(els) - 80} more; pass `query` to narrow.")
    return "\n".join(lines)
