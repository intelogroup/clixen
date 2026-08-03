"""
Reminder tool — schedule a Telegram message at a future datetime.

Depends on telegram_bot._scheduler (AsyncIOScheduler) and
telegram_bot._fire_reminder being available at runtime.
TELEGRAM_OWNER_CHAT_ID must be set in .env.
"""
import os

SET_REMINDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_reminder",
        "description": (
            "Schedule a reminder to be sent to the user via Telegram at a specific date and time. "
            "Use this whenever the user says 'remind me', 'don't forget', 'alert me', or asks to "
            "be notified at a future time. Always confirm the parsed time back to the user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The reminder message to send",
                },
                "iso_datetime": {
                    "type": "string",
                    "description": (
                        "When to fire the reminder, in ISO 8601 format with timezone offset. "
                        "Example: 2026-04-07T13:50:00-05:00. "
                        "If the user gives a relative time like 'in 10 minutes', compute the absolute datetime."
                    ),
                },
            },
            "required": ["text", "iso_datetime"],
        },
    },
}


def set_reminder(text: str, iso_datetime: str) -> str:
    """
    Schedule a one-shot Telegram reminder via APScheduler.
    Thread-safe: add_job() is safe to call from executor threads.
    """
    from datetime import datetime, timezone
    import telegram_bot  # runtime import to access _scheduler + _fire_reminder

    chat_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "")
    if not chat_id:
        return "[reminder error] TELEGRAM_OWNER_CHAT_ID not set in .env"

    if telegram_bot._scheduler is None or not telegram_bot._scheduler.running:
        return "[reminder error] Scheduler not running — is the bot started?"

    try:
        dt = datetime.fromisoformat(iso_datetime)
        # Ensure timezone-aware; if naive, assume local
        if dt.tzinfo is None:
            dt = dt.astimezone()
        now = datetime.now(timezone.utc).astimezone(dt.tzinfo)
        if dt <= now:
            return f"[reminder error] Datetime '{iso_datetime}' is in the past."

        telegram_bot._scheduler.add_job(
            telegram_bot._fire_reminder,
            trigger="date",
            run_date=dt,
            args=[chat_id, text],
            misfire_grace_time=300,  # fire up to 5 min late if bot was briefly down
        )
        return f"Reminder set: '{text}' at {dt.strftime('%b %d %I:%M %p %Z')}"
    except ValueError as e:
        return f"[reminder error] Could not parse datetime '{iso_datetime}': {e}"
    except Exception as e:
        return f"[reminder error] {e}"
