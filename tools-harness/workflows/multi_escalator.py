"""
Multi-Channel Alert Escalator — 6-step conditional pipeline.
1. Trigger (Gmail search or webhook)
2. iMessage alert (fast channel)
3. Sleep (wait for acknowledgment window)
4. Check if acknowledgment received (via Gmail reply)
5. If no ack: escalate to Telegram
6. If still no response: escalate to email + notification
"""

from workflows.pipeline import (
    GmailSearchStep, iMessageSendStep, TelegramSendStep, EmailSendStep,
    NotificationStep, SleepStep, ConditionStep, run_pipeline, PipelineStep,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_multi_escalator",
        "description": (
            "Create a 6-step multi-channel alert escalator: sends iMessage "
            "immediately, waits for acknowledgment, escalates to Telegram, "
            "then email if unanswered. Use for critical alerts that must "
            "not be missed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient for initial alert",
                },
                "email_recipient": {
                    "type": "string",
                    "description": "Email for final escalation",
                },
                "alert_message": {
                    "type": "string",
                    "description": "Alert message text",
                },
                "ack_keyword": {
                    "type": "string",
                    "description": "Keyword to check in Gmail replies as ack, default 'ack'",
                    "default": "ack",
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "Seconds to wait for ack before escalation, default 120",
                    "default": 120,
                },
                "gmail_query": {
                    "type": "string",
                    "description": "Gmail query to check for acknowledgment replies",
                    "default": "subject:Re newer_than:1h",
                },
            },
            "required": ["imessage_recipient", "email_recipient", "alert_message"],
        },
    },
}


def create_multi_escalator(
    imessage_recipient: str,
    email_recipient: str,
    alert_message: str,
    ack_keyword: str = "ack",
    wait_seconds: int = 120,
    gmail_query: str = "subject:Re newer_than:1h",
) -> str:
    from tools.create_watcher import create_watcher
    action_config = {
        "imessage_recipient": imessage_recipient,
        "email_recipient": email_recipient,
        "alert_message": alert_message,
        "ack_keyword": ack_keyword,
        "wait_seconds": wait_seconds,
        "gmail_query": gmail_query,
    }
    return create_watcher(
        name="Alert Escalator",
        trigger_type="schedule",
        trigger_config={"interval_seconds": wait_seconds},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {"pipeline": "multi_escalator", **action_config},
            "notify_via": "notification",
        },
        schedule={"interval_seconds": wait_seconds},
    )


class CheckAckStep(PipelineStep):
    def __init__(self, gmail_query: str, ack_keyword: str, output_key: str = "ack_received"):
        self.gmail_query = gmail_query
        self.ack_keyword = ack_keyword
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        from tools.gmail import list_emails
        result = list_emails(query=self.gmail_query, max_results=10)
        if "error" in result.lower():
            ctx[self.output_key] = "error"
            return {"ok": False, "output": "gmail error"}
        import re
        bodies = re.findall(r"(?:Body|Snippet):\s*(.+?)(?=\n\n|\Z)", result, re.DOTALL)
        acked = any(self.ack_keyword.lower() in b.lower() for b in bodies)
        ctx[self.output_key] = "acked" if acked else "no_ack"
        return {"ok": acked, "output": "ack received" if acked else "no ack"}


def execute(imessage_recipient: str, email_recipient: str,
            alert_message: str, ack_keyword: str = "ack",
            wait_seconds: int = 120,
            gmail_query: str = "subject:Re newer_than:1h") -> dict:
    steps = [
        # Step 1: iMessage immediate alert
        iMessageSendStep(
            recipient=imessage_recipient,
            message=alert_message,
        ),

        # Step 2: Wait for ack window
        SleepStep(seconds=wait_seconds),

        # Step 3: Check if ack received
        CheckAckStep(gmail_query=gmail_query, ack_keyword=ack_keyword),

        # Step 4: Condition — if no ack, escalate to Telegram
        ConditionStep(
            key="ack_received",
            match_values=["acked"],
            if_match="NotificationStep",
            if_not="TelegramSendStep",
        ),

        # Step 5: Telegram escalation
        TelegramSendStep(
            message="ESCALATION: No acknowledgment for alert.\n{}".format(alert_message[:500]),
        ),

        # Step 6: Sleep + check again before email
        SleepStep(seconds=wait_seconds),
        CheckAckStep(gmail_query=gmail_query, ack_keyword=ack_keyword),

        # Step 7: Condition — if still no ack, email
        ConditionStep(
            key="ack_received",
            match_values=["acked"],
            if_match="NotificationStep",
            if_not="EmailSendStep",
        ),

        # Step 8: Email final escalation
        EmailSendStep(
            to=email_recipient,
            subject="CRITICAL: Alert not acknowledged",
            body="Alert: {}\n\nNo acknowledgment received via iMessage or Telegram.".format(alert_message),
        ),

        # Step 9: Log
        NotificationStep(
            message="Alert escalator: final escalation sent to {}".format(email_recipient),
            level="error",
        ),
    ]
    return run_pipeline(steps)


if __name__ == "__main__":
    import sys
    im = sys.argv[1] if len(sys.argv) > 1 else input("iMessage recipient: ")
    em = sys.argv[2] if len(sys.argv) > 2 else input("Email recipient: ")
    msg = sys.argv[3] if len(sys.argv) > 3 else input("Alert message: ")
    result = execute(im, em, msg, wait_seconds=10)
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status}")
