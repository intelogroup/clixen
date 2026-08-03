"""
iMessage Reminder — Schedule → iMessage notification.

Use case: Daily/weekly reminder sent as iMessage at specific time

Usage:
    from workflows.imessage_reminder import create_imessage_reminder
    create_imessage_reminder(recipient="+1234567890", hour=9, message="Take meds")
"""

from tools.create_watcher import create_watcher

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_imessage_reminder",
        "description": (
            "Create a scheduled iMessage reminder: sends an iMessage to a phone "
            "number or email at a recurring time (daily/weekly). "
            "Use when: user wants text reminders via iMessage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {
                    "type": "string",
                    "description": "iMessage recipient — phone number (+1...) or Apple ID email",
                },
                "message": {
                    "type": "string",
                    "description": "Reminder message text",
                },
                "hour": {
                    "type": "integer",
                    "description": "Hour to send (0-23), default 9",
                    "default": 9,
                    "minimum": 0,
                    "maximum": 23,
                },
                "minute": {
                    "type": "integer",
                    "description": "Minute to send (0-59), default 0",
                    "default": 0,
                    "minimum": 0,
                    "maximum": 59,
                },
                "timezone": {
                    "type": "string",
                    "description": "Timezone, default 'America/New_York'",
                    "default": "America/New_York",
                },
            },
            "required": ["recipient", "message"],
        },
    },
}


def create_imessage_reminder(
    recipient: str,
    message: str,
    hour: int = 9,
    minute: int = 0,
    timezone: str = "America/New_York",
) -> str:
    """Create a scheduled iMessage reminder."""
    trigger_config = {
        "hour": hour,
        "minute": minute,
        "timezone": timezone,
    }
    action_config = {
        "recipient": recipient,
        "message": message,
    }
    return create_watcher(
        name=f"iMessage Reminder ({hour}:{minute:02d})",
        trigger_type="schedule",
        trigger_config=trigger_config,
        action_type="imessage",
        action_config=action_config,
        schedule={"interval_seconds": 3600},
    )


def run_manual():
    recipient = input("Recipient phone/email: ") or "+1234567890"
    msg = input("Message: ") or "Test reminder from clixen"
    result = create_imessage_reminder(recipient=recipient, message=msg)
    print(result)
    return result


if __name__ == "__main__":
    run_manual()
