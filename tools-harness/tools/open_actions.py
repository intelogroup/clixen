"""
Direct "open this app / URL" actions for the orb — one `open` call in the user's
real, logged-in apps. No subagent hop, no clicking or typing (that is
ask_browser_agent's job). Fence: http/https URLs only; apps must be installed.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from tools.orb_cursor import point_at_app
from tools.url_guard import navigable, refusal

_APP_DIRS = (
    Path("/Applications"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path("/System/Library/CoreServices"),  # Finder lives here
    Path("/Applications/Utilities"),
    Path.home() / "Applications",
)

OPEN_URL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "open_url",
        "description": (
            "Open a web page in the user's own browser (default Google Chrome), as a new tab in "
            "their existing logged-in session, and bring it to the front. Use for 'go to chrome "
            "and amazon', 'open youtube', 'take me to my bank'. Pass the site's real https URL "
            "(e.g. amazon -> https://www.amazon.com). To show search results, build the site's own "
            "search URL (e.g. https://en.wikipedia.org/w/index.php?search=enigma+machine, "
            "https://www.amazon.com/s?k=headphones, https://www.youtube.com/results?search_query=lofi) "
            "and open it with this tool. It only loads a page: no clicking, typing, logging in or "
            "buying inside it. Prefer this over ask_browser_agent whenever the goal is just to "
            "show the user a page or results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full http(s) URL."},
                "app": {"type": "string", "description": "Browser app name.", "default": "Google Chrome"},
            },
            "required": ["url"],
        },
    },
}

OPEN_APP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "open_app",
        "description": (
            "Launch or bring to the front an installed NATIVE macOS app by name (e.g. 'Notes', "
            "'Spotify', 'Calculator'). Websites (YouTube, Gmail, Amazon, Wikipedia...) are NOT apps: "
            "use open_url for those, not this. Does nothing else in the app."
        ),
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "App name as shown in Finder."}},
            "required": ["name"],
        },
    },
}


def valid_url(url: str) -> bool:
    return navigable(url) and url.strip() != "about:blank"


def _app_installed(name: str) -> bool:
    n = name.removesuffix(".app")
    return any((d / f"{n}.app").exists() for d in _APP_DIRS)


def _open(args: list[str]) -> str | None:
    """Run `open`; return an error string or None."""
    try:
        r = subprocess.run(["/usr/bin/open", *args], capture_output=True, text=True, timeout=10)
    except Exception as e:
        return str(e)
    return None if r.returncode == 0 else (r.stderr.strip() or f"open exited {r.returncode}")


def norm_url(u: str) -> str:
    """URL identity for 'is this the same page': no scheme, no www., no fragment, no trailing slash."""
    u = u.strip().lower().split("#", 1)[0]
    u = u.split("://", 1)[-1]
    u = u[4:] if u.startswith("www.") else u
    return u.rstrip("/")


def same_page(current: str, wanted: str) -> bool:
    return bool(current) and norm_url(current) == norm_url(wanted)


def _front_url(app: str) -> str:
    """URL of the page showing in `app`'s front window, read from the accessibility tree: it follows
    the window the user actually sees, whichever Chrome instance or profile owns it (AppleScript and
    the debugging port each address only one of them)."""
    try:
        from tools.ax_context import front_url

        return front_url(app)
    except Exception:
        return ""


# (pid, site) -> the tab-strip element of the tab we last opened a page of that site in. `open -a` always
# makes a new tab, so without this every open_url piles one up; keyed by site so YouTube and Wikipedia
# each keep their own tab. In-memory on purpose: core.py is one long-lived process, and a tab we cannot
# vouch for (after a restart) is simply not reused.
_work_tab: dict[tuple[int, str], object] = {}


def site_of(url: str) -> str:
    """'en.wikipedia.org/wiki/X' -> 'wikipedia.org'. shortcut: last two labels, so co.uk-style suffixes share a site; upgrade with the public-suffix list if that bites."""
    host = norm_url(url).split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]
    return ".".join(host.split(".")[-2:])


def _tab_list(app: str) -> list:
    try:
        from tools.ax_context import tab_elements

        return tab_elements(app)
    except Exception:
        return []


def _pid(app: str):
    try:
        from tools.ax_context import _target_app

        return _target_app(app)[0]
    except Exception:
        return None


def _remember_working_tab(app: str, before: list, url: str) -> None:
    """The tab that appeared because of our `open` becomes the one to reuse."""
    pid = _pid(app)
    if pid is None:
        return
    time.sleep(1.0)
    new = [t for t in _tab_list(app) if not any(t == b for b in before)]
    if len(new) == 1:
        _work_tab[(pid, site_of(url))] = new[0]


def _reuse_working_tab(app: str, url: str) -> bool:
    """Navigate the tab we opened earlier instead of opening another. False when there is none (closed, or never opened)."""
    pid = _pid(app)
    key = (pid, site_of(url))
    tab = _work_tab.get(key)
    if tab is None:
        return False
    if not any(tab == t for t in _tab_list(app)):  # the user closed it
        _work_tab.pop(key, None)
        return False
    from tools.ax_context import activate_app, select_tab

    if not select_tab(tab) or not activate_app(app):
        return False
    time.sleep(0.3)
    typed = url.replace("\\", "\\\\").replace('"', '\\"')
    script = (f'tell application "System Events" to tell (first process whose unix id is {pid})\n'
              f'keystroke "l" using {{command down}}\ndelay 0.2\nkeystroke "{typed}"\ndelay 0.1\nkey code 36\nend tell')
    try:
        r = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=15)
    except Exception:
        return False
    return r.returncode == 0


def open_url(url: str, app: str = "Google Chrome") -> str:
    url = url.strip()
    if not valid_url(url):
        return refusal(url)
    if not _app_installed(app):
        return f"App not found: {app}."
    if same_page(_front_url(app), url):
        point_at_app(app)
        return f"{url} is already showing in {app}: not opened again."
    if _reuse_working_tab(app, url):
        point_at_app(app)
        return f"Opened {url} in {app}, reusing the tab it opened earlier for this site (no new tab)."
    before = _tab_list(app)
    err = _open(["-a", app, url])
    if err:
        return f"Error opening {url} in {app}: {err}"
    _remember_working_tab(app, before, url)
    point_at_app(app)  # the orb's cursor flies to the window we just raised
    return f"Opened {url} in {app}."


def open_app(name: str) -> str:
    if not _app_installed(name):
        return f"App not found: {name}."
    err = _open(["-a", name])
    if err:
        return f"Error opening {name}: {err}"
    point_at_app(name)
    return f"Opened {name}."
