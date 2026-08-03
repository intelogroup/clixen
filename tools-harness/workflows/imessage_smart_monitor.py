"""
iMessage Smart Monitor — multi-step conditional pipeline.
Step 1: Search Gmail for recent emails matching query
Step 2: AI classify each email urgency (urgent/normal/low)
Step 3: Route urgent emails to iMessage + Telegram
Step 4: Route normal emails to Telegram only
Step 5: Log notification in-app
"""

from workflows.pipeline import (
    GmailSearchStep,
    AiClassifyStep,
    ConditionStep,
    iMessageSendStep,
    TelegramSendStep,
    NotificationStep,
    run_pipeline,
    PipelineStep,
)

import os

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_imessage_smart_monitor",
        "description": (
            "Create a smart Gmail monitor: searches Gmail for emails matching "
            "a query, AI-classifies urgency, sends urgent ones via iMessage (fast "
            "notification) and all via Telegram. Runs every N minutes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient for urgent alerts",
                },
                "gmail_query": {
                    "type": "string",
                    "description": "Gmail search, e.g. 'from:boss is:unread'",
                },
                "poll_interval_seconds": {
                    "type": "integer",
                    "description": "Poll interval in seconds, default 300",
                    "default": 300,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max emails to check, default 10",
                    "default": 10,
                },
            },
            "required": ["imessage_recipient", "gmail_query"],
        },
    },
}


def create_imessage_smart_monitor(
    imessage_recipient: str,
    gmail_query: str,
    poll_interval_seconds: int = 300,
    max_results: int = 10,
) -> str:
    """Create a smart Gmail monitor watcher."""
    from tools.create_watcher import create_watcher

    action_config = {
        "imessage_recipient": imessage_recipient,
        "gmail_query": gmail_query,
        "max_results": max_results,
    }
    return create_watcher(
        name=f"Smart Monitor: {gmail_query}",
        trigger_type="schedule",
        trigger_config={"query": gmail_query},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {"pipeline": "imessage_smart_monitor", **action_config},
            "notify_via": "notification",
        },
        schedule={"interval_seconds": poll_interval_seconds},
    )


class ParseEmailBatchesStep(PipelineStep):
    """Split Gmail raw output into per-email batch items for individual classification."""

    def __init__(self, input_key: str, output_key: str):
        self.input_key = input_key
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        import re
        raw = ctx.get(self.input_key, "")
        emails = []
        for block in raw.split("\n\n"):
            subj = re.search(r"Subject:\s*(.+)", block)
            fro = re.search(r"From:\s*(.+)", block)
            body = re.search(r"(?:Body|Snippet):\s*(.+)", block, re.DOTALL)
            if subj:
                emails.append({
                    "subject": subj.group(1).strip(),
                    "from": fro.group(1).strip() if fro else "?",
                    "body": (body.group(1).strip()[:200] if body else ""),
                })
        ctx[self.output_key] = emails
        return {"ok": True, "output": f"{len(emails)} emails parsed"}


class ClassifyAndRouteStep(PipelineStep):
    """Classify each email, send urgent via iMessage + Telegram, normal via Telegram."""

    def __init__(self, emails_key: str, imessage_recipient: str):
        self.emails_key = emails_key
        self.imessage_recipient = imessage_recipient

    def run(self, ctx: dict) -> dict:
        import httpx
        emails = ctx.get(self.emails_key, [])
        urgent_sent = 0
        normal_sent = 0

        for email in emails:
            text = f"From: {email['from']}\nSubject: {email['subject']}\n{email['body']}"
            prompt = (
                "Classify email urgency into exactly one word: URGENT NORMAL LOW\n"
                f"{text}\n\nCategory:"
            )
            try:
                resp = httpx.post(
                    "http://127.0.0.1:11434/api/generate",
                    json={"model": os.environ.get("OLLAMA_REWRITE_MODEL", "qwen3.5:4b"), "prompt": prompt, "stream": False,
                           "options": {"num_predict": 10, "temperature": 0.1}},
                    timeout=15,
                )
                label = resp.json().get("response", "").strip().lower()
            except Exception:
                label = "normal"

            msg = f"[{label.upper()}] {email['from']}: {email['subject']}"
            from tools.telegram_send import send_telegram
            send_telegram(msg)

            if "urgent" in label:
                from tools.imessage_search import send as send_imessage
                send_imessage(
                    to=self.imessage_recipient,
                    message=f"URGENT: {email['from']}\n{email['subject']}",
                )
                urgent_sent += 1
            else:
                normal_sent += 1

        ctx["urgent_count"] = urgent_sent
        ctx["normal_count"] = normal_sent
        return {"ok": True, "output": f"{urgent_sent} urgent (iMessage+TG), {normal_sent} normal (TG)"}


def execute(imessage_recipient: str, gmail_query: str = "is:unread",
            max_results: int = 10) -> dict:
    steps = [
        GmailSearchStep(query=gmail_query, max_results=max_results),
        ParseEmailBatchesStep(input_key="gmail_raw", output_key="parsed_emails"),
        ClassifyAndRouteStep(emails_key="parsed_emails", imessage_recipient=imessage_recipient),
        NotificationStep(message_key="urgent_count", message="{urgent_count} urgent, {normal_count} normal"),
    ]
    return run_pipeline(steps)


if __name__ == "__main__":
    import sys
    recipient = sys.argv[1] if len(sys.argv) > 1 else input("iMessage recipient: ")
    query = sys.argv[2] if len(sys.argv) > 2 else input("Gmail query (default from:me): ") or "from:me"
    result = execute(recipient, query)
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status} -> {str(r.get('output', r.get('error', '')))[:100]}")
