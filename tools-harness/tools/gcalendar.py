"""
Google Calendar tools — list events, create events, delete events.

Requires calendar scope — re-auth if not yet granted:
    python tools/google_auth.py --auth
"""

from datetime import datetime, timezone, timedelta
from tools.google_auth import execute_with_google_auth_retry, get_service

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

LIST_EVENTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_calendar_events",
        "description": (
            "List upcoming events from Google Calendar. "
            "Defaults to the next 7 days. Can filter by keyword or date range."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days ahead to look (default 14, max 90)",
                    "default": 14,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max events to return (default 10)",
                    "default": 10,
                },
                "query": {
                    "type": "string",
                    "description": "Optional keyword filter (searches event titles/descriptions)",
                    "default": "",
                },
            },
            "required": [],
        },
    },
}

CREATE_EVENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_calendar_event",
        "description": (
            "Create a new Google Calendar event. "
            "Use for scheduling meetings, appointments, or any time-based event. "
            "REQUIRED: title and start_iso must come from the user — never invent them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Event title — must come from the user, never invent one.",
                },
                "start_iso": {
                    "type": "string",
                    "description": (
                        "Start datetime in ISO 8601 with timezone offset. "
                        "Must come from the user (exact or relative like 'next Monday at 2pm'). "
                        "Infer timezone offset from current time context — do NOT ask the user. "
                        "If unsure, use UTC (Z). Example: 2026-04-10T14:00:00-04:00"
                    ),
                },
                "end_iso": {
                    "type": "string",
                    "description": (
                        "End datetime in ISO 8601 with timezone offset. "
                        "If omitted, defaults to 1 hour after start. "
                        "Infer from duration if the user specifies it."
                    ),
                    "default": "",
                },
                "description": {
                    "type": "string",
                    "description": "Optional event description / notes",
                    "default": "",
                },
                "location": {
                    "type": "string",
                    "description": "Optional location string",
                    "default": "",
                },
            },
            "required": ["title", "start_iso"],
        },
    },
}

DELETE_EVENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_calendar_event",
        "description": "Delete a Google Calendar event by its event ID (from list_calendar_events).",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Calendar event ID returned by list_calendar_events",
                },
            },
            "required": ["event_id"],
        },
    },
}

# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def list_calendar_events(days_ahead: int = 14, max_results: int = 10, query: str = "") -> str:
    svc = get_service("calendar", "v3")
    if svc is None:
        return "[calendar error] Not authenticated. Run: python tools/google_auth.py --auth"
    try:
        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=min(days_ahead, 90))).isoformat()

        kwargs = dict(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=min(max_results, 50),
            singleEvents=True,
            orderBy="startTime",
        )
        if query:
            kwargs["q"] = query

        result = execute_with_google_auth_retry(
            lambda: get_service("calendar", "v3").events().list(**kwargs).execute()
        )
        events = result.get("items", [])
        if not events:
            return "No upcoming events found."

        lines = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date", "?"))
            # Format datetime for readability
            try:
                dt = datetime.fromisoformat(start)
                start_fmt = dt.strftime("%a %b %d, %I:%M %p %Z")
            except Exception:
                start_fmt = start
            location = e.get("location", "")
            desc = e.get("description", "")[:80]
            lines.append(
                f"ID: {e['id']}\n"
                f"  Title: {e.get('summary', '(no title)')}\n"
                f"  Start: {start_fmt}\n"
                + (f"  Location: {location}\n" if location else "")
                + (f"  Notes: {desc}\n" if desc else "")
            )
        return "\n".join(lines)
    except Exception as e:
        return f"[calendar error] list_calendar_events failed: {e}"


def create_calendar_event(
    title: str,
    start_iso: str,
    end_iso: str = "",
    description: str = "",
    location: str = "",
) -> str:
    svc = get_service("calendar", "v3")
    if svc is None:
        return "[calendar error] Not authenticated. Run: python tools/google_auth.py --auth"
    try:
        start_dt = datetime.fromisoformat(start_iso)
        if not end_iso:
            end_dt = start_dt + timedelta(hours=1)
            end_iso = end_dt.isoformat()

        _tz = str(start_dt.tzinfo or "UTC")
        event = {
            "summary": title,
            "start": {"dateTime": start_iso, "timeZone": _tz},
            "end": {"dateTime": end_iso, "timeZone": _tz},
        }
        if description:
            event["description"] = description
        if location:
            event["location"] = location

        created = execute_with_google_auth_retry(
            lambda: get_service("calendar", "v3")
            .events()
            .insert(calendarId="primary", body=event)
            .execute()
        )
        start_fmt = start_dt.strftime("%a %b %d, %I:%M %p")
        return f"Event created: '{title}' on {start_fmt} (ID: {created['id']})"
    except Exception as e:
        return f"[calendar error] create_calendar_event failed: {e}"


def check_calendar_conflict(at: datetime, buffer_minutes: int = 60, end: datetime | None = None) -> str | None:
    """Conflict check for automation gating (e.g. auto-accept logic) — is
    there an existing event within `buffer_minutes` of the [at, end] window?
    `end` defaults to `at` (point-in-time) when the assignment has no known
    duration; pass it whenever the assignment has an end time, or an event
    starting after `at` but before the assignment ends (e.g. assignment
    10:40AM-1:40PM, other event at 12:50PM) is silently missed.
    Returns None if clear, or the conflicting event's title+time as a string.
    Not exposed as an LLM tool (no schema) — internal helper, reuses the same
    auth/service plumbing as list_calendar_events instead of a second client.
    """
    svc = get_service("calendar", "v3")
    if svc is None:
        return "[calendar error] Not authenticated. Run: python tools/google_auth.py --auth"
    try:
        window_start = (at - timedelta(minutes=buffer_minutes)).astimezone(timezone.utc).isoformat()
        window_end = ((end or at) + timedelta(minutes=buffer_minutes)).astimezone(timezone.utc).isoformat()
        result = execute_with_google_auth_retry(
            lambda: get_service("calendar", "v3").events().list(
                calendarId="primary", timeMin=window_start, timeMax=window_end,
                singleEvents=True, maxResults=5,
            ).execute()
        )
        events = result.get("items", [])
        if not events:
            return None
        e = events[0]
        start = e["start"].get("dateTime", e["start"].get("date", "?"))
        return f"{e.get('summary', '(no title)')} at {start}"
    except Exception as e:
        return f"[calendar error] check_calendar_conflict failed: {e}"


def delete_calendar_event(event_id: str) -> str:
    svc = get_service("calendar", "v3")
    if svc is None:
        return "[calendar error] Not authenticated. Run: python tools/google_auth.py --auth"
    try:
        execute_with_google_auth_retry(
            lambda: get_service("calendar", "v3")
            .events()
            .delete(calendarId="primary", eventId=event_id)
            .execute()
        )
        return f"Event {event_id} deleted."
    except Exception as e:
        return f"[calendar error] delete_calendar_event failed: {e}"


def get_todays_events_raw_status() -> tuple[list[dict], str]:
    """Return today's calendar events plus an error string when unavailable.

    Each dict has keys: id, title, start_time (ISO str), end_time (ISO str),
    location (str), all_day (bool).  Only events that start or overlap today
    in local time are included.  Max 20 events.
    """
    svc = get_service("calendar", "v3")
    if svc is None:
        return [], "Not authenticated. Run: python tools/google_auth.py --auth"
    try:
        local_now = datetime.now().astimezone()
        today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = local_now.replace(hour=23, minute=59, second=59, microsecond=999999)

        result = execute_with_google_auth_retry(
            lambda: get_service("calendar", "v3")
            .events()
            .list(
                calendarId="primary",
                timeMin=today_start.isoformat(),
                timeMax=today_end.isoformat(),
                maxResults=20,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = result.get("items", [])
        out = []
        for e in events:
            raw_start = e["start"].get("dateTime", e["start"].get("date", ""))
            raw_end = e["end"].get("dateTime", e["end"].get("date", ""))
            all_day = "dateTime" not in e["start"]
            # Normalise to ISO string (keep as-is — already ISO)
            out.append({
                "id": e.get("id", ""),
                "title": e.get("summary", ""),
                "start_time": raw_start,
                "end_time": raw_end,
                "location": e.get("location", ""),
                "all_day": all_day,
            })
        return out, ""
    except Exception as exc:
        return [], str(exc)


def get_todays_events_raw() -> list[dict]:
    """Return today's calendar events as a list of dicts (not formatted text).

    Returns [] on auth failure or API error.
    """
    events, _ = get_todays_events_raw_status()
    return events
