"""Console ring buffer shared by every browser driver.

Two drivers create pages — `tools/browser.py` (headless/incognito) and
`tools/headed_browser.py` (the visible Chrome the agent drives) — and both used to
attach `page.on("console", lambda _: None)`, i.e. deliberately discard the output.
That made "audit this site's console" impossible from either side even though a
page object was right there. Keeping the buffer in one place means a new driver
cannot forget it.

Scope, stated plainly: this captures the browser the tool drives, not the Chrome
window the user happens to be looking at. That one is reached only through
AppleScript/AX, which cannot see console at all.
"""

from __future__ import annotations

_MAX = 300
_ENTRIES: list[dict] = []


def record(message) -> None:
    """Append a Playwright console message (never raises: logging must not break a page)."""
    try:
        _ENTRIES.append({
            "type": getattr(message, "type", "log"),
            "text": (getattr(message, "text", "") or "")[:500],
            "location": (getattr(getattr(message, "location", None), "url", "") or "")[:200],
        })
    except Exception:
        return
    del _ENTRIES[:-_MAX]


def record_error(exc) -> None:
    _ENTRIES.append({"type": "pageerror", "text": str(exc)[:500], "location": ""})
    del _ENTRIES[:-_MAX]


def clear() -> None:
    _ENTRIES.clear()


def messages(limit: int = 50, level: str = "") -> str:
    want = (level or "").strip().lower()
    rows = [m for m in _ENTRIES if not want or m["type"] == want]
    if not rows:
        scope = f" (level={want})" if want else ""
        return f"No console output captured yet{scope}. Open a page with browser_open first."
    tail = rows[-max(1, min(int(limit or 50), 200)):]
    lines = [f"Console ({len(rows)} captured, showing {len(tail)}):"]
    for m in tail:
        where = f"  [{m['location']}]" if m["location"] else ""
        lines.append(f"- {m['type']}: {m['text']}{where}")
    errors = sum(1 for m in rows if m["type"] in ("error", "pageerror"))
    if errors:
        lines.append(f"{errors} error-level message(s) — that is the audit signal.")
    return "\n".join(lines)
