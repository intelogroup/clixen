"""
Daily Business Digest — Schedule → Gmail summary + Telegram notification.

Use case: Daily at specified time → summarize calendar + tasks + emails → send digest

Usage:
    from workflows.daily_digest import create_daily_digest
    create_daily_digest(hour=8, include_calendar=True, include_tasks=True)
"""

from tools.create_watcher import create_watcher

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_daily_digest",
        "description": (
            "Create a daily business digest: runs on a schedule and sends a summary "
            "of calendar events, Google Tasks, and unread emails to Telegram. "
            "Use when: user wants a morning briefing of their day."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hour": {
                    "type": "integer",
                    "description": "Hour to send digest (0-23), default 8",
                    "default": 8,
                    "minimum": 0,
                    "maximum": 23,
                },
                "minute": {
                    "type": "integer",
                    "description": "Minute to send digest (0-59), default 0",
                    "default": 0,
                    "minimum": 0,
                    "maximum": 59,
                },
                "include_calendar": {
                    "type": "boolean",
                    "description": "Include today's calendar events?",
                    "default": True,
                },
                "include_tasks": {
                    "type": "boolean",
                    "description": "Include pending Google Tasks?",
                    "default": True,
                },
                "include_inbox": {
                    "type": "boolean",
                    "description": "Include unread email count?",
                    "default": True,
                },
                "telegram_message": {
                    "type": "string",
                    "description": "Message template",
                    "default": (
                        "📅 Calendar: {events}\n📋 Tasks: {tasks}\n📧 Inbox: {unread} unread"
                    ),
                },
            },
        },
    },
}


def create_daily_digest(
    hour: int = 8,
    minute: int = 0,
    include_calendar: bool = True,
    include_tasks: bool = True,
    include_inbox: bool = True,
    telegram_message: str = None,
) -> str:
    """Create a daily digest watcher."""
    import datetime
    import time

    # Calculate interval_seconds from time
    # Simple approach: run hourly and check if it's the right hour
    interval_seconds = 3600  # Check every hour

    now = datetime.datetime.now()
    target_hour = hour

    msg = telegram_message or (
        "📅 Today's Calendar ({calendar_count} events)\n{calendar_summary}\n\n"
        "📋 Pending Tasks ({tasks_count})\n{tasks_summary}\n\n"
        "📧 Inbox ({unread_count} unread)"
    )

    trigger_config = {
        "hour": hour,
        "minute": minute,
        "include_calendar": include_calendar,
        "include_tasks": include_tasks,
        "include_inbox": include_inbox,
    }

    action_config = {"message": msg}

    # For simplicity, use hourly with hour check
    return create_watcher(
        name=f"Daily Digest ({hour}:{minute:02d})",
        trigger_type="schedule",
        trigger_config=trigger_config,
        action_type="telegram",
        action_config=action_config,
        schedule={"interval_seconds": 3600},  # Check every hour
    )


def run_manual():
    """Test runner for manual testing."""
    hour = int(input("Enter hour (0-23, default 8): ") or "8")
    result = create_daily_digest(hour=hour)
    print(result)
    return result


if __name__ == "__main__":
    run_manual()
