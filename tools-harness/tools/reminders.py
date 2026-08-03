"""Native macOS Reminders + Calendar via AppleScript (with icalBuddy fast path).

Avoids PyObjC + EventKit's permission UX (which prompts on every cold launch
in many setups). Reminders + Calendar both have first-class AppleScript support.

For Calendar reads we prefer `icalBuddy` (brew install ical-buddy) — orders of
magnitude faster than AppleScript when iCloud/Exchange calendars are present.
Falls back to AppleScript when the binary isn't found.

Reminders:
  list_reminders(list_name="", show_completed=False)
  create_reminder(title, due="", list_name="", notes="")

Calendar:
  list_calendar_events(days=7)

First call may trigger a one-time Automation permission prompt.
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta


_TIMEOUT_S = 10
_CALENDAR_TIMEOUT_S = 45  # Calendar iteration over many accounts is slow.


def _fmt_native_datetime(dt: datetime) -> str:
    """Format a datetime for AppleScript's free-form date parser, portably.

    macOS strftime supports %-d/%-I (no padding); Linux glibc does not and
    raises ValueError. Build the string by hand so the module never crashes
    on the date-format step on either platform."""
    month = dt.strftime("%B")
    day = int(dt.strftime("%d"))  # strip leading zero
    year = dt.strftime("%Y")
    hour_12 = int(dt.strftime("%I")) % 12 or 12
    minute = dt.strftime("%M")
    second = dt.strftime("%S")
    ampm = dt.strftime("%p")
    return f"{month} {day}, {year} {hour_12}:{minute}:{second} {ampm}"


def _osa(script: str, timeout_s: int = _TIMEOUT_S) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout_s,
        )
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", "osascript timed out"
    except OSError as e:
        return 1, "", str(e)


def _q(s: str) -> str:
    """Escape a Python string for safe embedding in an AppleScript double-quoted literal."""
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


# ─── Reminders ────────────────────────────────────────────────────────────

LIST_REMINDERS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_reminders",
        "description": (
            "List reminders from Apple Reminders. Use for 'what reminders do I have?', "
            "'show me my todo list', 'what's on my Groceries list?'. "
            "Returns title, due date, and list name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "list_name": {
                    "type": "string",
                    "description": "Optional: limit to a specific list (e.g. 'Reminders', 'Groceries').",
                    "default": "",
                },
                "show_completed": {
                    "type": "boolean",
                    "description": "Include completed reminders. Default false.",
                    "default": False,
                },
            },
            "required": [],
        },
    },
}

CREATE_REMINDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_reminder",
        "description": (
            "Create a reminder in Apple Reminders. Use for 'remind me to X', "
            "'add buy milk to my list', 'set a reminder for tomorrow 3pm'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Reminder text."},
                "due": {
                    "type": "string",
                    "description": (
                        "Due date in ISO format (YYYY-MM-DD or YYYY-MM-DD HH:MM). "
                        "Omit for no date."
                    ),
                    "default": "",
                },
                "list_name": {
                    "type": "string",
                    "description": "Target list. Default: the user's default list.",
                    "default": "",
                },
                "notes": {
                    "type": "string",
                    "description": "Longer body / notes for the reminder.",
                    "default": "",
                },
            },
            "required": ["title"],
        },
    },
}


def list_reminders(list_name: str = "", show_completed: bool = False) -> str:
    completed_filter = "" if show_completed else " whose completed is false"
    if list_name:
        scope = f'list "{_q(list_name)}"'
    else:
        scope = "(every list)"
    script = (
        'set out to ""\n'
        'tell application "Reminders"\n'
        + (
            f'  set theLists to {{list "{_q(list_name)}"}}\n'
            if list_name else
            '  set theLists to (every list)\n'
        ) +
        '  repeat with L in theLists\n'
        f'    repeat with R in (reminders of L{completed_filter})\n'
        '      set t to name of R\n'
        '      set d to ""\n'
        '      try\n'
        '        if (due date of R) is not missing value then\n'
        '          set d to (due date of R) as string\n'
        '        end if\n'
        '      end try\n'
        '      set out to out & (name of L) & " | " & t & " | " & d & linefeed\n'
        '    end repeat\n'
        '  end repeat\n'
        'end tell\n'
        'return out'
    )
    rc, stdout, stderr = _osa(script)
    if rc != 0:
        if "not authorized" in stderr.lower() or "1743" in stderr:
            return "[reminders error] Automation permission denied. Grant in System Settings → Privacy & Security → Automation."
        return f"[reminders error] {stderr or 'unknown'}"
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        scope_str = f" in '{list_name}'" if list_name else ""
        state = "" if show_completed else " open"
        return f"No{state} reminders{scope_str}."
    head = f"{len(lines)} reminder(s):"
    return head + "\n" + "\n".join(f"  {ln}" for ln in lines)


def create_reminder(title: str, due: str = "", list_name: str = "", notes: str = "") -> str:
    title = (title or "").strip()
    if not title:
        return "[reminders error] title required."

    # Build property list as AppleScript record.
    props = [f'name:"{_q(title)}"']
    if notes:
        props.append(f'body:"{_q(notes)}"')
    if due:
        try:
            # Accept "YYYY-MM-DD" or "YYYY-MM-DD HH:MM"
            if " " in due:
                dt = datetime.strptime(due, "%Y-%m-%d %H:%M")
            else:
                dt = datetime.strptime(due, "%Y-%m-%d").replace(hour=9)
        except ValueError:
            return f"[reminders error] could not parse due date {due!r}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM."
        # AppleScript's date constructor accepts a free-form string via `date "..."`.
        as_date = _fmt_native_datetime(dt)
        props.append(f'due date:(date "{as_date}")')

    target_list = (
        f'list "{_q(list_name)}"' if list_name else "(default list)"
    )
    script = (
        'tell application "Reminders"\n'
        f'  set newR to make new reminder at {target_list} with properties {{{", ".join(props)}}}\n'
        '  return name of newR\n'
        'end tell'
    )
    rc, stdout, stderr = _osa(script)
    if rc != 0:
        if "not authorized" in stderr.lower() or "1743" in stderr:
            return "[reminders error] Automation permission denied. Grant in System Settings → Privacy & Security → Automation."
        return f"[reminders error] {stderr or 'unknown'}"
    return f"Created reminder: {stdout or title}"


# ─── Calendar ─────────────────────────────────────────────────────────────

LIST_CAL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_mac_calendar_events",
        "description": (
            "List events from the native macOS Calendar app for the next N days. "
            "Use for 'what's on my calendar', 'meetings this week', 'agenda for tomorrow'. "
            "Reads iCloud + Exchange + local calendars."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days ahead to include (1–30). Default 7.",
                    "default": 7,
                },
            },
            "required": [],
        },
    },
}


def _ensure_calendar_running(wait_s: float = 3.0) -> None:
    """Launch Calendar.app if not running, poll briefly. Best-effort — a
    failure here just means the AppleScript call below fails as before."""
    import sys
    import time
    _, out, _ = _osa('tell application "System Events" to (name of processes) contains "Calendar"')
    if out == "true":
        return
    if sys.platform == "darwin":
        subprocess.run(["open", "-a", "Calendar"], capture_output=True, timeout=5)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        _, out, _ = _osa('tell application "System Events" to (name of processes) contains "Calendar"')
        if out == "true":
            return
        time.sleep(0.2)


def _list_calendar_events_icalbuddy(days: int) -> str | None:
    """Fast path via icalBuddy. Returns None if the binary isn't installed."""
    if shutil.which("icalBuddy") is None:
        return None
    # eventsToday+N gives today through N days ahead.
    span = f"eventsToday+{days - 1}" if days > 1 else "eventsToday"
    cmd = [
        "icalBuddy",
        "-ea",          # exclude all-day flag noise
        "-nc",          # no calendar name in title
        "-li", "0",     # no limit
        "-tf", "%H:%M",
        "-df", "%Y-%m-%d",
        "-b", "  ",
        "-iep", "title,datetime,location",
        span,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"[calendar error] icalBuddy failed: {e}"
    if result.returncode != 0:
        msg = (result.stderr or "").strip().splitlines()[-1] if result.stderr else ""
        return f"[calendar error] icalBuddy exited {result.returncode}: {msg}"
    out = (result.stdout or "").strip()
    if not out:
        return f"No events in the next {days} day(s)."
    return f"Calendar events (next {days} day(s), via icalBuddy):\n{out}"


def list_calendar_events(days: int = 7) -> str:
    days = max(1, min(int(days or 7), 30))

    # Prefer icalBuddy — iterates iCloud/Exchange calendars in milliseconds.
    fast = _list_calendar_events_icalbuddy(days)
    if fast is not None:
        return fast

    # Fallback: AppleScript (slow on multi-account Macs). Calendar.app must be
    # running for "tell application" to work — launch it if needed (confirmed
    # live: repeated "-600 Application isn't running" errors when it wasn't).
    _ensure_calendar_running()
    now = datetime.now()
    end = now + timedelta(days=days)
    start_str = _fmt_native_datetime(now)
    end_str = _fmt_native_datetime(end)
    script = (
        f'set startDate to date "{start_str}"\n'
        f'set endDate to date "{end_str}"\n'
        'set out to ""\n'
        'tell application "Calendar"\n'
        '  repeat with C in calendars\n'
        '    try\n'
        '      set evs to (every event of C whose start date ≥ startDate and start date ≤ endDate)\n'
        '      repeat with E in evs\n'
        '        set out to out & (name of C) & " | " & ((start date of E) as string) & " | " & (summary of E) & linefeed\n'
        '      end repeat\n'
        '    end try\n'
        '  end repeat\n'
        'end tell\n'
        'return out'
    )
    rc, stdout, stderr = _osa(script, timeout_s=_CALENDAR_TIMEOUT_S)
    if rc == 124:
        return (
            f"[calendar error] iterating all Calendar accounts took >{_CALENDAR_TIMEOUT_S}s. "
            "If you have many cloud calendars, consider using the Google Calendar tool "
            "(list_calendar_events) instead, or shorten the day range."
        )
    if rc != 0:
        if "not authorized" in stderr.lower() or "1743" in stderr:
            return "[calendar error] Automation permission denied. Grant in System Settings → Privacy & Security → Automation."
        return f"[calendar error] {stderr or 'unknown'}"
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return f"No events in the next {days} day(s)."
    return f"{len(lines)} event(s) in next {days} day(s):\n" + "\n".join(f"  {ln}" for ln in lines)
