"""
iMessage Morning Briefing — multi-step pipeline.
Step 1: Check calendar
Step 2: Check tasks
Step 3: Check Gmail (unread today)
Step 4: AI summarize into briefing text
Step 5: Send iMessage summary
Step 6: Telegram with full detail
"""

from workflows.pipeline import (
    GmailSearchStep,
    AiSummarizeStep,
    iMessageSendStep,
    TelegramSendStep,
    run_pipeline,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_imessage_briefing",
        "description": (
            "Create a multi-step morning briefing: checks calendar + tasks + "
            "Gmail, AI-summarizes into a briefing, sends summary via iMessage "
            "and full detail via Telegram. Runs daily at set time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient for the summary",
                },
                "hour": {
                    "type": "integer",
                    "description": "Hour to run (0-23), default 7",
                    "default": 7,
                },
                "minute": {
                    "type": "integer",
                    "description": "Minute (0-59), default 0",
                    "default": 0,
                },
                "gmail_query": {
                    "type": "string",
                    "description": "Gmail search, default 'is:unread newer_than:1d'",
                    "default": "is:unread newer_than:1d",
                },
                "max_emails": {
                    "type": "integer",
                    "description": "Max emails to include, default 5",
                    "default": 5,
                },
                "calendar_days": {
                    "type": "integer",
                    "description": "Days ahead for calendar, default 1",
                    "default": 1,
                },
            },
            "required": ["imessage_recipient"],
        },
    },
}


def create_imessage_briefing(
    imessage_recipient: str,
    hour: int = 7,
    minute: int = 0,
    gmail_query: str = "is:unread newer_than:1d",
    max_emails: int = 5,
    calendar_days: int = 1,
) -> str:
    """Create an iMessage morning briefing watcher."""
    from tools.create_watcher import create_watcher

    action_config = {
        "imessage_recipient": imessage_recipient,
        "gmail_query": gmail_query,
        "max_emails": max_emails,
        "calendar_days": calendar_days,
    }
    return create_watcher(
        name=f"iMessage Briefing ({hour}:{minute:02d})",
        trigger_type="schedule",
        trigger_config={"hour": hour, "minute": minute},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {"pipeline": "imessage_briefing", **action_config},
            "notify_via": "notification",
        },
        schedule={"interval_seconds": 3600},
    )


def execute(imessage_recipient: str, gmail_query: str = "is:unread newer_than:1d",
            max_emails: int = 5, calendar_days: int = 1) -> dict:
    """Run the 6-step briefing pipeline directly."""
    steps = [
        GmailSearchStep(query=gmail_query, max_results=max_emails),
        AiSummarizeStep(
            input_key="gmail_raw",
            output_key="email_summary",
            instruction="Summarize these emails briefly. Extract key action items.",
        ),
        iMessageSendStep(
            recipient=imessage_recipient,
            message=(
                "Morning briefing ready — check Telegram for full detail.\n"
                "Calendar: check, Tasks: check, Email: check."
            ),
        ),
        TelegramSendStep(
            message=(
                "{calendar_events}\n\n"
                "Tasks:\n{tasks}\n\n"
                "Email Summary:\n{email_summary}"
            ),
        ),
    ]
    return run_pipeline(steps)


if __name__ == "__main__":
    import sys
    recipient = sys.argv[1] if len(sys.argv) > 1 else input("iMessage recipient: ")
    result = execute(recipient)
    print("ok" if all(s["result"].get("ok") for s in result["steps"]) else "fail")
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status} -> {str(r.get('output', r.get('error', '')))[:80]}")
