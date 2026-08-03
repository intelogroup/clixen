"""
Meeting Prep — Calendar → pre-meeting notification + task.

Use case: X minutes before meeting → send agenda link + prep notes to participants

Usage:
    from workflows.meeting_prep import create_meeting_prep
    create_meeting_prep(minutes_before=15)
"""

from tools.create_watcher import create_watcher

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_meeting_prep",
        "description": (
            "Create a meeting prep watcher: sends a notification X minutes before calendar events "
            "with meeting details, link, and any prep notes. "
            "Use when: user wants reminders before meetings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "minutes_before": {
                    "type": "integer",
                    "description": "Minutes before meeting to notify, default 15",
                    "default": 15,
                },
                "include_link": {
                    "type": "boolean",
                    "description": "Include meeting link (Zoom/Meet)?",
                    "default": True,
                },
                "include_agenda": {
                    "type": "boolean",
                    "description": "Include meeting description?",
                    "default": True,
                },
                "include_attendees": {
                    "type": "boolean",
                    "description": "Include attendee list?",
                    "default": True,
                },
                "telegram_message": {
                    "type": "string",
                    "description": "Message template",
                    "default": "⏰ Meeting in {minutes} min: {title}\n{link}\n📋 {agenda}",
                },
            },
        },
    },
}


def create_meeting_prep(
    minutes_before: int = 15,
    include_link: bool = True,
    include_agenda: bool = True,
    include_attendees: bool = True,
    telegram_message: str = None,
) -> str:
    """Create a meeting prep watcher."""
    msg_template = telegram_message or (
        "⏰ Meeting in {minutes} min: {title}\n{meeting_link}\n📋 {agenda}\n👥 {attendees}"
    )

    trigger_config = {
        "minutes_before": minutes_before,
        "include_link": include_link,
        "include_agenda": include_agenda,
        "include_attendees": include_attendees,
    }

    action_config = {"message": msg_template}

    # For simplicity, use short polling interval
    poll_seconds = max(minutes_before * 60, 60)  # At least every minute

    return create_watcher(
        name=f"Meeting Prep ({minutes_before}min)",
        trigger_type="schedule",
        trigger_config=trigger_config,
        action_type="telegram",
        action_config=action_config,
        schedule={"interval_seconds": poll_seconds},
    )


def run_manual():
    """Test runner for manual testing."""
    minutes = int(input("Minutes before (default 15): ") or "15")
    result = create_meeting_prep(minutes_before=minutes)
    print(result)
    return result


if __name__ == "__main__":
    run_manual()
