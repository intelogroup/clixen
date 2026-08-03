"""
get_current_time tool — returns the current local datetime with timezone.
Useful for mid-session time queries or when the model needs a fresh reading
(e.g. "what time is it in Tokyo?", "how long until 3pm?").
"""
from datetime import datetime, timezone

GET_CURRENT_TIME_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": (
            "Get the current local date, time, AND TIMEZONE from the user's computer. "
            "Returns: local day/date/time/timezone + UTC reference + optional named timezone. "
            "MUST call this BEFORE creating any calendar event, task, or reminder with relative "
            "dates (tomorrow, next week, in 2 days). Use the returned local timezone offset "
            "when computing ISO 8601 timestamps for event creation. "
            "NEVER guess or hardcode dates — always call this tool first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": (
                        "Optional IANA timezone name e.g. 'America/New_York', 'Asia/Tokyo'. "
                        "Defaults to local system timezone from the user's computer if omitted."
                    ),
                    "default": "",
                }
            },
            "required": [],
        },
    },
}


def get_current_time(timezone_name: str = "") -> str:
    now_local = datetime.now().astimezone()
    result = (
        f"Local:  {now_local.strftime('%A, %B %d %Y, %I:%M:%S %p %Z')} "
        f"(UTC{now_local.strftime('%z')})\n"
        f"UTC:    {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    if timezone_name:
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(timezone_name)
            now_tz = datetime.now(tz)
            result += f"\n{timezone_name}: {now_tz.strftime('%A, %B %d %Y, %I:%M:%S %p %Z')}"
        except Exception as e:
            result += f"\n[timezone error] {timezone_name}: {e}"
    return result
