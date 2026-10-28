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

import re
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

# Controls a form filler cares about. Inline links can also be zero-height and are fine
# to leave alone — flagging those would cry "scroll" on every page.
def is_rendered(role: str, height: float) -> bool:
    """False for a zero-height form control: Chrome keeps it in the tree inside the window
    rect with h=0, so it reads as on-screen while being below the fold. Inline links can be
    zero-height too and are deliberately NOT flagged, or every page would cry 'scroll'."""
    return not (height <= 0 and role in _FORM_ROLES)


_FORM_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox",
               "AXCheckBox", "AXRadioButton", "AXButton", "AXPopUpButton"}


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
    for attr in ("AXTitle", "AXDescription", "AXPlaceholderValue", "AXValue", "AXHelp"):  # placeholder: YouTube's search box has no other name
        v = _get(el, attr)
        if isinstance(v, str) and v.strip():
            return " ".join(v.split())
    return _child_text(el)


def _child_text(el, depth: int = 3) -> str:
    """Text of the element's own children: web links often carry it only there."""
    parts = []
    for kid in _get(el, "AXChildren") or ():
        v = _get(kid, "AXValue") if _get(kid, "AXRole") == "AXStaticText" else None
        v = v if isinstance(v, str) else _get(kid, "AXTitle")
        if isinstance(v, str) and v.strip():
            parts.append(v)
        elif depth > 1:
            parts.append(_child_text(kid, depth - 1))
    return " ".join(" ".join(parts).split())


def _target_app(name: str = ""):
    """(pid, localized name) of the named app, else the front-most one."""
    from AppKit import NSWorkspace

    ws = NSWorkspace.sharedWorkspace()
    if name:
        apps = [a for a in ws.runningApplications() if (a.localizedName() or "").lower() == name.lower()]
        # Two Chromes (the user's profile and the agent profile): the one with the most tabs is the one the user works in.
        # `open -a` and isActive() flip between them unpredictably, so neither decides.
        apps.sort(key=lambda a: (-tab_count(a.processIdentifier()), not a.isActive()) if len(apps) > 1 else 0)
        a = apps[0] if apps else None
    else:
        a = ws.frontmostApplication()
    return (a.processIdentifier(), a.localizedName() or "") if a else (None, "")


def activate_app(name: str) -> bool:
    """Bring the instance `_target_app` picks to the front (NSRunningApplication, not `open -a`, which picks any instance)."""
    from AppKit import NSWorkspace

    pid, _ = _target_app(name)
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if a.processIdentifier() == pid:
            a.activateWithOptions_(2)  # NSApplicationActivateIgnoringOtherApps
            time.sleep(0.6)
            return True
    return False


def _ax_app(pid: int):
    from ApplicationServices import AXUIElementCreateApplication, AXUIElementSetAttributeValue

    el = AXUIElementCreateApplication(pid)
    # Chrome turns its web tree on when a client asks the application element for its role
    # and refuses the manual switch; Electron takes AXManualAccessibility (the set reports an
    # error but still flips it on). Both are cheap, so ask both (browser-use/macos-harness).
    _get(el, "AXRole")
    AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
    AXUIElementSetAttributeValue(el, "AXEnhancedUserInterface", True)
    return el


def _window_rect(win) -> tuple[float, float, float, float] | None:
    p, s = _point(_get(win, "AXPosition")), _size(_get(win, "AXSize"))
    return (p[0], p[1], s[0], s[1]) if p and s else None


_WARM: set[int] = set()  # apps whose accessibility tree has already answered with elements


def _retry_walk(nodes: int, found: int, attempt: int, warm: bool) -> bool:
    """Chromium may still be building its tree on the very first call: wait and walk once more, but only when the
    walk was tiny AND found nothing, and never for an app that has already answered. A small real page is not slow."""
    return nodes <= 200 and found == 0 and attempt == 0 and not warm


def elements(app: str = "", query: str = "", offscreen: bool = False) -> tuple[str, str, list[dict]]:
    """(app name, window title, on-screen actionable elements) of the target app's focused
    window. Each element: {role, label, x, y, w, h, el} with x,y the centre. `offscreen`
    also keeps elements scrolled out of view (for scroll_into_view). `web` marks entries inside the page's content (not the browser's own toolbar, tab strip or extensions)."""
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
    stack = [(win, False, False)]  # (element, inside a web area, inside an extension panel's web area)
    n = 0
    # Chromium may still be building its tree on the very first call: retry once if the walk is tiny.
    for attempt in range(2):
        while stack and n < _MAX_NODES and time.monotonic() < deadline:
            el, in_web, in_panel = stack.pop()
            n += 1
            role = _get(el, "AXRole")
            if role in _ACTIONABLE:
                p, s = _point(_get(el, "AXPosition")), _size(_get(el, "AXSize"))
                # below the fold Chrome reports height 0: keep those when scrolling will fix it
                if p and s and s[0] > 0 and (s[1] > 0 or offscreen):
                    cx, cy = p[0] + s[0] / 2, p[1] + s[1] / 2
                    inside = rect is None or (rect[0] <= cx <= rect[0] + rect[2] and rect[1] <= cy <= rect[1] + rect[3])
                    label = _label(el)
                    key = (role, label, round(cx), round(cy))
                    if (inside or offscreen) and key not in seen and (label or role in ("AXTextField", "AXTextArea", "AXSearchField")) and (not q or q in label.lower()):
                        seen.add(key)
                        out.append({
                            "role": role[2:], "label": label,
                            "x": round(cx), "y": round(cy), "w": round(s[0]), "h": round(s[1]),
                            "el": el, "web": in_web, "panel": in_panel,
                            # Where this sits relative to the window. "Not visible" is not
                            # "not there": measured 2026-10-25, the orb concluded a page had
                            # no 2025 issues because every card was below the fold and the
                            # filter dropped them silently.
                            # A zero-height control is in the tree but NOT rendered: Chrome
                            # reports it inside the window rect with h=0, so onscreen=True and
                            # the agent typed at a zero-height line. Measured on info-chir's
                            # submission form: Email * was (814x0, onscreen) while below the
                            # fold. Treat it as off-screen so the next step is scrolling.
                            "unrendered": s[1] <= 0 and role in _FORM_ROLES,
                            "onscreen": bool(inside) and is_rendered(role, s[1]),
                            "above": bool(rect and cy < rect[1]),
                            "below": bool(rect and cy > rect[1] + rect[3]),
                        })
            kids = _get(el, "AXChildren")
            if kids:
                if role == "AXWebArea" and not in_panel:
                    in_panel = str(_get(el, "AXURL") or "").startswith("chrome-extension://")
                in_web = in_web or role == "AXWebArea"
                stack.extend((k, in_web, in_panel) for k in reversed(kids))  # keep document order
        if not _retry_walk(n, len(out), attempt, pid in _WARM):
            break
        time.sleep(1.2)
        stack, n = [(win, False, False)], 0
    if out:
        _WARM.add(pid)
    return name, title, out


def window_title(app: str = "") -> str:
    """Title of the target app's focused window, cheap (no tree walk). A popover (bookmark bubble,
    extension popup) is not the window: its title is skipped in favour of the real window under it."""
    pid, _ = _target_app(app)
    if pid is None:
        return ""
    el = _ax_app(pid)
    win = _get(el, "AXFocusedWindow") or _get(el, "AXMainWindow")
    if win is not None and _get(win, "AXSubrole") not in (None, "AXStandardWindow"):
        win = _get(el, "AXMainWindow") or win
    return (_get(win, "AXTitle") or "") if win is not None else ""


def popovers(app: str = "") -> list[str]:
    """Titles of the target app's transient floating windows (bookmark bubble, extension popup, zoom bubble)."""
    pid, _ = _target_app(app)
    if pid is None:
        return []
    out = []
    for w in _get(_ax_app(pid), "AXWindows") or ():
        if _get(w, "AXSubrole") not in ("AXStandardWindow", "AXDialog", "AXSystemDialog"):
            out.append(str(_get(w, "AXTitle") or "(untitled popover)"))
    return out


_NOT_EDITABLE = {"AXButton", "AXLink", "AXWebArea", "AXStaticText", "AXImage", "AXMenuItem", "AXMenuButton", "AXRadioButton", "AXCheckBox", "AXTab"}


def focused_field(app: str = "") -> tuple[str, str] | None:
    """(role, value) of the keyboard-focused element of the target app, or None when it can't be read."""
    pid, _ = _target_app(app)
    if pid is None:
        return None
    el = _get(_ax_app(pid), "AXFocusedUIElement")
    if el is None:
        return None
    v = _get(el, "AXValue")
    return str(_get(el, "AXRole") or ""), v if isinstance(v, str) else ""


def focused_label(app: str = "") -> str:
    """Accessibility label of the keyboard-focused element ('' when unreadable)."""
    pid, _ = _target_app(app)
    el = _get(_ax_app(pid), "AXFocusedUIElement") if pid is not None else None
    return _label(el) if el is not None else ""


_APPLESCRIPT_URL = {
    # Safari's AX tree exposes no AXURL on the web area, so the accessibility read
    # returns "" and every "is this page on screen?" check answers No. Verified
    # 2026-10-25: AX empty, AppleScript correct.
    #
    # Chrome is deliberately NOT in this table: AppleScript addresses one Chrome
    # instance while AX sees whichever window is in front, and with two profiles
    # running that made `tabs` describe a browser the user was not looking at.
    "safari": 'tell application "{app}" to get URL of current tab of front window',
}


def _applescript_front_url(app: str) -> str:
    script = _APPLESCRIPT_URL.get((app or "").lower())
    if not script:
        return ""
    try:
        import subprocess

        out = subprocess.run(
            ["osascript", "-e", script.format(app=app)], capture_output=True, text=True, timeout=8
        )
        return out.stdout.strip()
    except Exception:
        return ""


def front_url(app: str = "") -> str:
    """URL of the page in the target app's front window ('' when it is not a web page)."""
    pid, name = _target_app(app)
    if pid is None:
        return ""
    el = _ax_app(pid)
    win = _get(el, "AXFocusedWindow") or _get(el, "AXMainWindow")
    if win is not None:
        url = _get(_web_area(win), "AXURL")
        if url:
            return str(url)
    # Browsers whose AX tree carries no URL: ask the browser, but only where that
    # cannot disagree with what is on screen (see _APPLESCRIPT_URL).
    return _applescript_front_url(name or app)



def front_context() -> dict:
    """A cheap snapshot of what is in front: app, window, URL, focus.

    Only the primitives that do NOT walk the element tree (`window_title`,
    `front_url`, `focused_field`, `popovers`), so this costs a few AX calls and
    no tab/AXChildren traversal — measured well under 100 ms. That is the point:
    the model can be told where it is for free, instead of spending a whole tool
    round on `observe()` just to find out.

    Fail-safe by design: any AX/AppKit problem returns {} and the caller simply
    omits the block. Never raises.
    """
    try:
        _pid, app = _target_app()
        if not app:
            return {}
        role, value = focused_field() or ("", "")
        return {
            "app": app,
            "window": window_title() or "",
            "url": front_url() or "",
            "focus_role": role,
            "focus_label": focused_label() or "",
            "focus_value": (value or "")[:120],
            "popovers": popovers(),
        }
    except Exception:  # noqa: BLE001 - a context nicety must never break a turn
        return {}


def tab_count(pid: int) -> int:
    """Tabs across all of one app instance's windows (0 when its windows have no tab strip)."""
    el = _ax_app(pid)
    n = 0
    for win in _get(el, "AXWindows") or ():
        stack = [win]
        for _ in range(400):  # the tab strip sits near the top of the tree
            if not stack:
                break
            e = stack.pop()
            if _get(e, "AXRole") == "AXTabGroup":
                n += len(_get(e, "AXChildren") or ())
                break
            stack.extend(_get(e, "AXChildren") or ())
    return n


def tab_elements(app: str = "") -> list:
    """Tab-strip entries (AX elements) of the target app's focused window, left to right."""
    pid, _ = _target_app(app)
    if pid is None:
        return []
    el = _ax_app(pid)
    win = _get(el, "AXFocusedWindow") or _get(el, "AXMainWindow")
    stack = [win] if win is not None else []
    for _ in range(600):  # the tab strip sits near the top of the tree
        if not stack:
            break
        e = stack.pop()
        if _get(e, "AXRole") == "AXTabGroup":
            return list(_get(e, "AXChildren") or ())
        stack.extend(_get(e, "AXChildren") or ())
    return []


def select_tab(tab) -> bool:
    """Bring a tab-strip entry to the front (press it)."""
    from ApplicationServices import AXUIElementPerformAction

    return AXUIElementPerformAction(tab, "AXPress") == 0


def settle_title(before: str, app: str = "", limit: float = 3.0, quiet: float = 0.4) -> str:
    """Poll the window title until it differs from `before` and holds still for `quiet`
    seconds, or `limit` runs out. Returns the last title seen."""
    end = time.monotonic() + limit
    cur, since = before, time.monotonic()
    while time.monotonic() < end:
        time.sleep(0.1)
        t = window_title(app)
        if t != cur:
            cur, since = t, time.monotonic()
        elif cur != before and time.monotonic() - since >= quiet:
            break
    return cur


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


def _frame(el) -> tuple[float, float, float, float] | None:
    p, s = _point(_get(el, "AXPosition")), _size(_get(el, "AXSize"))
    return (p[0], p[1], s[0], s[1]) if p and s else None


def _cut(a, b):
    """The part of rect `a` inside rect `b`, None when nothing is left."""
    if a is None or b is None:
        return a
    x, y = max(a[0], b[0]), max(a[1], b[1])
    w, h = min(a[0] + a[2], b[0] + b[2]) - x, min(a[1] + a[3], b[1] + b[3]) - y
    return (x, y, w, h) if w > 0 and h > 0 else None


def _visible_centre(el, win_rect, strict: bool = False) -> tuple[float, float] | None:
    """Centre of the part of `el` inside its window and every scroll area above it, so a
    wrapped or half-hidden link is clicked where it can actually be hit. `strict` rejects an
    element that is mostly cut off: a 1px sliver under a sticky header is not clickable (a
    click there lands on whatever covers it)."""
    full = _frame(el)
    part = _cut(full, win_rect)
    parent = _get(el, "AXParent")
    for _ in range(40):
        if part is None or parent is None:
            break
        if _get(parent, "AXRole") == "AXScrollArea":
            part = _cut(part, _frame(parent))
        parent = _get(parent, "AXParent")
    if part and strict and full and full[3] > 0 and (part[3] < 0.6 * full[3] or part[3] < 6):
        return None
    return (part[0] + part[2] / 2, part[1] + part[3] / 2) if part else None


def _scroll_into_view(el) -> None:
    """Ask the app to scroll `el` on screen (pattern from browser-use/macos-harness)."""
    from ApplicationServices import AXUIElementCopyParameterizedAttributeValue, AXUIElementPerformAction

    if "AXScrollToVisible" in (_actions(el) or ()):
        AXUIElementPerformAction(el, "AXScrollToVisible")
    parent = _get(el, "AXParent")
    for _ in range(40):
        if parent is None:
            return
        if _get(parent, "AXRole") == "AXScrollArea":
            AXUIElementCopyParameterizedAttributeValue(parent, "AXScrollToShowDescendant", el, None)
            return
        parent = _get(parent, "AXParent")


def _actions(el):
    from ApplicationServices import AXUIElementCopyActionNames

    err, names = AXUIElementCopyActionNames(el, None)
    return names if err == 0 else None


class AmbiguousTarget(Exception):
    """More than one element contains the wanted text and none matches it exactly."""


def labels_at(x: float, y: float) -> list[str]:
    """Labels of the element under a screen point and its three nearest ancestors (what a click there would hit)."""
    from ApplicationServices import AXUIElementCopyElementAtPosition, AXUIElementCreateSystemWide

    err, el = AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), x, y, None)
    out = []
    for _ in range(4):
        if el is None:
            break
        if lab := _label(el):
            out.append(lab)
        el = _get(el, "AXParent")
    return out


def find_element(label: str, app: str = "") -> tuple[float, float] | None:
    """Clickable point of the best element whose label matches: exact > shortest substring.
    Scrolls it into view first and aims at its visible part."""
    pid, _ = _target_app(app)
    _, _, els = elements(app, label, offscreen=True)
    els = [e for e in els if e["web"]] or els  # a toolbar button or tab titled like the target is not the page link; browser chrome only when the page has no match
    w = label.strip().lower()
    app_el = _ax_app(pid)
    win = _get(app_el, "AXFocusedWindow") or _get(app_el, "AXMainWindow")
    rect = _window_rect(win) if win else None
    best = None
    if w and not any(e["label"].lower() == w for e in els):
        names = sorted({e["label"] for e in els}, key=lambda n: (len(n), n))  # short labels are controls; the caller shows only the first few
        if len(names) > 1:
            raise AmbiguousTarget(names)
    for e in els:
        lab = e["label"].lower()
        score = (0 if lab == w else 1, 0 if _visible_centre(e["el"], rect, strict=True) else 1, len(lab))
        if best is None or score < best[0]:
            best = (score, e["el"])
    if best is None:
        return None
    el = best[1]
    pt = _visible_centre(el, rect, strict=True)
    if pt is None:  # off-screen or mostly cut off: ask the app to bring it in, then measure again
        _scroll_into_view(el)
        time.sleep(0.4)
        pt = _visible_centre(el, rect, strict=True) or _visible_centre(el, rect)
    return pt


def side_panel_open(els) -> bool:
    return any(e["label"].lower() == "close side panel" for e in els)


def toolbar_extensions(els) -> list[str]:
    """Names of the browser extensions pinned to the toolbar ('Monica: All-In-One AI ... Has access to this site' -> 'Monica')."""
    return [re.sub(r"\s*has access to.*$", "", e["label"].split(":")[0], flags=re.I).strip()
            for e in els if e["role"] == "PopUpButton" and "has access to" in e["label"].lower()]


def get_window_context(query: str = "", app: str = "") -> str:
    if _screen_locked():
        return "Screen is locked."
    name, title, els = elements(app, query)
    if name and is_blocked(name, title):
        return "Front window is private or a password manager; not reading it."
    lines = [f"Front app: {name} — {title or '(no focused window)'}"]
    if pops := popovers(app):
        lines.append(f"POPOVER OPEN: {', '.join(pops)} (floats over the page; elements below are its own. Close it with press_keys('esc') or click_text on its button.)")
    if side_panel_open(els):
        lines.append("SIDE PANEL OPEN (an extension such as Thunderbit/Monica, or a Chrome panel) beside the page; it can cover or confuse clicks. "
                     "Close it with close_extension_panels (it clicks the panel's X).")
    if ext := toolbar_extensions(els):
        lines.append(f"Browser extensions in the toolbar (click_text its name to open): {', '.join(ext)}")
    lines.append("Windows:")
    lines += [f"- {w}" for w in window_list()[:12]]
    if not els:
        lines.append("Elements: none exposed (app has no accessibility tree; try get_screen_context or click_text OCR).")
    else:
        lines.append(f"Elements ({len(els)}, x,y = centre in screen points):")
        lines += [f"- {e['role']} '{e['label'][:60]}' ({e['x']},{e['y']}) {e['w']}x{e['h']}" for e in els[:80]]
        if len(els) > 80:
            lines.append(f"… {len(els) - 80} more; pass `query` to narrow.")
    return "\n".join(lines)


# ---- whole-page text -------------------------------------------------------------------------------

_BLOCKS = {"AXGroup", "AXRow", "AXCell", "AXList", "AXHeading", "AXParagraph", "AXTable", "AXLandmark", "AXListItem", "AXArticle"}
_FLUSH = object()


def _walk_text(root, deadline: float) -> list[str]:
    """Visible-or-not text of a subtree in document order: one line per block, headings marked."""
    lines: list[str] = []
    cur: list[str] = []

    def flush() -> None:
        t = " ".join(" ".join(cur).split())
        cur.clear()
        if t and (not lines or lines[-1] != t):
            lines.append(t)

    stack = [root]
    n = 0
    while stack and n < 80000 and time.monotonic() < deadline:
        el = stack.pop()
        if el is _FLUSH:
            flush()
            continue
        n += 1
        role = _get(el, "AXRole")
        if role == "AXStaticText":
            v = _get(el, "AXValue")
            if isinstance(v, str) and v.strip():
                cur.append(v)
        elif role in ("AXImage", "AXButton", "AXCheckBox", "AXRadioButton", "AXTextField", "AXTextArea"):
            v = _get(el, "AXValue") if role in ("AXTextField", "AXTextArea") else None
            lab = v if isinstance(v, str) and v.strip() else (_get(el, "AXTitle") or _get(el, "AXDescription"))
            if isinstance(lab, str) and lab.strip() and not _get(el, "AXChildren"):
                cur.append(lab)
        if role == "AXHeading":
            flush()
            cur.append("#")
        kids = _get(el, "AXChildren")
        if role in _BLOCKS:
            stack.append(_FLUSH)
        if kids:
            stack.extend(reversed(kids))
        if role == "AXHeading":
            pass
    flush()
    return lines


def _web_area(win):
    stack = [win]
    for _ in range(4000):
        if not stack:
            return win
        el = stack.pop()
        if _get(el, "AXRole") == "AXWebArea":
            return el
        stack.extend(reversed(_get(el, "AXChildren") or ()))
    return win


_CHROME_LANDMARKS = ("AXLandmarkBanner", "AXLandmarkNavigation", "AXLandmarkSearch", "AXLandmarkContentInfo")


def _main_landmark(root, deadline: float):
    """The page's <main> element, or None. Collapsed sections worth opening live in it; the footer's
    country/language pickers and the header's menus do not, and pressing them navigates away."""
    stack, n = [root], 0
    while stack and n < 80000 and time.monotonic() < deadline:
        el = stack.pop()
        n += 1
        if _get(el, "AXSubrole") == "AXLandmarkMain":
            return el
        stack.extend(reversed(_get(el, "AXChildren") or ()))
    return None


def _collapsed(root, deadline: float) -> list:
    """Elements that report AXExpanded == False and are safe to press open: disclosure triangles and
    plain expanders. Anything that opens a popup (menus, dropdowns) is left alone."""
    found, stack, n = [], [_main_landmark(root, deadline) or root], 0
    while stack and n < 80000 and time.monotonic() < deadline:
        el = stack.pop()
        n += 1
        role = _get(el, "AXRole")
        # Site chrome is not a collapsed section: pressing Amazon's "Deliver to" or "Go" reopens the
        # location popup / submits a search every time the page is read.
        if _get(el, "AXSubrole") in _CHROME_LANDMARKS:
            continue
        if role in ("AXDisclosureTriangle", "AXButton", "AXGroup", "AXHeading") and _get(el, "AXExpanded") is False \
                and not _get(el, "AXHasPopup") and "AXPress" in (_actions(el) or ()):
            found.append(el)
        stack.extend(reversed(_get(el, "AXChildren") or ()))
    return found


def page_text(app: str = "", expand: bool = True) -> tuple[str, str, list[str], int]:
    """(title, url, text lines, sections opened) of the whole page in the target app's front window,
    including text scrolled out of view. With `expand`, collapsed sections are pressed open first."""
    from ApplicationServices import AXUIElementPerformAction

    pid, _ = _target_app(app)
    if pid is None:
        return "", "", [], 0
    win = _get(_ax_app(pid), "AXFocusedWindow") or _get(_ax_app(pid), "AXMainWindow")
    if win is None:
        return "", "", [], 0
    title = _get(win, "AXTitle") or ""
    root = _web_area(win)
    deadline = time.monotonic() + 10.0
    opened = 0
    if expand:
        for el in _collapsed(root, deadline)[:12]:
            if AXUIElementPerformAction(el, "AXPress") == 0:
                opened += 1
        if opened:
            time.sleep(0.5)  # let the page render what it just opened
    url = _get(root, "AXURL")
    lines = _walk_text(root, deadline)
    if len(lines) < 3:  # Chromium may still be building its tree on the first call
        time.sleep(1.2)
        lines = _walk_text(root, time.monotonic() + 10.0)
    return title, str(url) if url else "", lines, opened
