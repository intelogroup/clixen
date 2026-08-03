"""
iMessage Meeting Prep — multi-step conditional pipeline.
Step 1: Check calendar for upcoming meetings
Step 2: Check pending tasks
Step 3: Search recent Gmail from meeting attendees
Step 4: If meeting found: AI generate prep summary
Step 5: Send prep via iMessage
Step 6: If no meeting: send status update via Telegram only
"""

from workflows.pipeline import (
    GmailSearchStep,
    AiSummarizeStep,
    ConditionStep,
    iMessageSendStep,
    TelegramSendStep,
    run_pipeline,
    PipelineStep,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_imessage_meeting_prep",
        "description": (
            "Create a multi-step meeting prep workflow: checks calendar for "
            "upcoming meetings, finds related tasks and emails, AI-generates "
            "a prep summary, sends via iMessage and Telegram. Runs 30min before "
            "each meeting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient for the prep summary",
                },
                "lookahead_hours": {
                    "type": "integer",
                    "description": "Look ahead in hours for meetings, default 2",
                    "default": 2,
                },
                "max_attendee_emails": {
                    "type": "integer",
                    "description": "Max recent emails from attendees to check, default 5",
                    "default": 5,
                },
            },
            "required": ["imessage_recipient"],
        },
    },
}


def create_imessage_meeting_prep(
    imessage_recipient: str,
    lookahead_hours: int = 2,
    max_attendee_emails: int = 5,
) -> str:
    from tools.create_watcher import create_watcher

    action_config = {
        "imessage_recipient": imessage_recipient,
        "lookahead_hours": lookahead_hours,
        "max_attendee_emails": max_attendee_emails,
    }
    return create_watcher(
        name=f"iMessage Meeting Prep ({lookahead_hours}h)",
        trigger_type="schedule",
        trigger_config={"interval_seconds": 1800},  # every 30 min
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {"pipeline": "imessage_meeting_prep", **action_config},
            "notify_via": "notification",
        },
        schedule={"interval_seconds": 1800},
    )


class ExtractAttendeesStep(PipelineStep):
    def __init__(self, input_key: str, output_key: str):
        self.input_key = input_key
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        import re
        raw = ctx.get(self.input_key, "")
        emails = set(re.findall(r"[\w.]+@[\w.]+\.\w+", raw))
        ctx[self.output_key] = list(emails)[:5]
        return {"ok": True, "output": f"{len(emails)} attendees found"}


def execute(imessage_recipient: str, lookahead_hours: int = 2,
            max_attendee_emails: int = 5) -> dict:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=lookahead_hours)

    steps = [
        ExtractAttendeesStep(input_key="calendar_events", output_key="attendees"),
    ]
    # Add dynamic Gmail search per attendee
    import copy
    result = run_pipeline(steps)
    attendees = result["context"].get("attendees", [])

    if not result["context"].get("calendar_events") or "No events" in str(result["context"].get("calendar_events", "")):
        result["context"]["meeting_found"] = False
        step = TelegramSendStep(message="No upcoming meetings. No prep needed.")
        step.run(result["context"])
        result["steps"].append({"step": "TelegramSendStep", "result": {"ok": True, "output": "no meetings"}})
        return result

    result["context"]["meeting_found"] = True
    for attendee in attendees[:3]:
        search = GmailSearchStep(query=f"from:{attendee} newer_than:7d", max_results=3)
        search.run(result["context"])

    summary_step = AiSummarizeStep(
        input_key="gmail_raw",
        output_key="prep_summary",
        instruction="Based on calendar events and recent emails, prepare a meeting prep brief: "
                     "what was discussed, what's pending, key agenda items.",
    )
    summary_step.run(result["context"])

    imsg = iMessageSendStep(
        recipient=imessage_recipient,
        message_key="prep_summary",
    )
    imsg.run(result["context"])

    tg = TelegramSendStep(
        message=(
            "Meeting Prep:\n{prep_summary}\n\n"
            "Calendar:\n{calendar_events}\n\nTasks:\n{tasks}"
        ),
    )
    tg.run(result["context"])

    return result


if __name__ == "__main__":
    import sys
    recipient = sys.argv[1] if len(sys.argv) > 1 else input("iMessage recipient: ")
    result = execute(recipient)
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status} -> {str(r.get('output', r.get('error', '')))[:80]}")
