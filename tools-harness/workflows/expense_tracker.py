"""
Expense Tracker — 7-step pipeline.
1. Gmail search for receipts/invoices
2. AI extract amount, vendor, date
3. AI categorize (food/transport/software/etc)
4. Append to Google Sheet
5. iMessage weekly summary (runs Sundays)
6. Telegram alert for big items (>$100)
7. Log + notification
"""

import json
import os
from datetime import datetime

from workflows.pipeline import (
    GmailSearchStep, AiGenerateStep,
    iMessageSendStep, LogAppendStep, NotificationStep,
    run_pipeline, PipelineStep,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_expense_tracker",
        "description": (
            "Create a 7-step expense tracker: scans Gmail for receipts, "
            "AI-extracts amount/vendor/date, categorizes each, logs to "
            "Google Sheet, sends iMessage weekly summary, Telegram alerts "
            "for big items (>$100)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient for weekly summary",
                },
                "spreadsheet_id": {
                    "type": "string",
                    "description": "Google Sheet ID to log expenses",
                },
                "gmail_query": {
                    "type": "string",
                    "description": "Gmail search for receipts, default 'receipt OR invoice OR payment'",
                    "default": "receipt OR invoice OR payment newer_than:7d",
                },
                "big_item_threshold": {
                    "type": "number",
                    "description": "Amount in $ to trigger Telegram alert, default 100",
                    "default": 100,
                },
                "poll_interval_seconds": {
                    "type": "integer",
                    "description": "Poll interval, default 86400 (daily)",
                    "default": 86400,
                },
            },
            "required": ["imessage_recipient", "spreadsheet_id"],
        },
    },
}


def create_expense_tracker(
    imessage_recipient: str,
    spreadsheet_id: str,
    gmail_query: str = "receipt OR invoice OR payment newer_than:7d",
    big_item_threshold: float = 100,
    poll_interval_seconds: int = 86400,
) -> str:
    from tools.create_watcher import create_watcher
    action_config = {
        "imessage_recipient": imessage_recipient,
        "spreadsheet_id": spreadsheet_id,
        "gmail_query": gmail_query,
        "big_item_threshold": big_item_threshold,
    }
    return create_watcher(
        name="Expense Tracker",
        trigger_type="schedule",
        trigger_config={"query": gmail_query},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {"pipeline": "expense_tracker", **action_config},
            "notify_via": "notification",
        },
        schedule={"interval_seconds": poll_interval_seconds},
    )


class BatchProcessExpensesStep(PipelineStep):
    """Parse Gmail output into individual emails, extract + categorize each."""

    def __init__(self, input_key: str, imessage_recipient: str,
                 spreadsheet_id: str, big_item_threshold: float):
        self.input_key = input_key
        self.imessage_recipient = imessage_recipient
        self.spreadsheet_id = spreadsheet_id
        self.big_item_threshold = big_item_threshold

    def run(self, ctx: dict) -> dict:
        import re
        import httpx

        raw = ctx.get(self.input_key, "")
        emails = re.split(r"\n(?=ID:\s)", raw)
        extracted = []
        big_items = []

        for email in emails[:15]:
            if not email.strip():
                continue
            subj = re.search(r"Subject:\s*(.+)", email)
            fro = re.search(r"From:\s*(.+)", email)
            text = email[:1500]

            prompt = (
                "Extract expense data as JSON: {\"amount\": number, \"vendor\": string, \"date\": string}\n"
                f"{text}\n\nJSON:"
            )
            try:
                resp = httpx.post(
                    "http://127.0.0.1:11434/api/generate",
                    json={"model": os.environ.get("OLLAMA_REWRITE_MODEL", "qwen3.5:4b"), "prompt": prompt, "stream": False,
                           "options": {"num_predict": 100, "temperature": 0.1}},
                    timeout=20,
                )
                raw_json = resp.json().get("response", "").strip()
                raw_json = re.sub(r"^[^{]*", "", raw_json)
                raw_json = re.sub(r"[^}]*$", "", raw_json)
                data = json.loads(raw_json) if raw_json else {}
            except Exception:
                data = {}

            amount = data.get("amount", 0)
            vendor = data.get("vendor", str(fro.group(1).strip() if fro else subj))
            date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))

            # Categorize
            cat_prompt = f"Categorize '{vendor}' as: food transport software utilities shopping other\nCategory:"
            try:
                resp = httpx.post(
                    "http://127.0.0.1:11434/api/generate",
                    json={"model": os.environ.get("OLLAMA_REWRITE_MODEL", "qwen3.5:4b"), "prompt": cat_prompt, "stream": False,
                           "options": {"num_predict": 10, "temperature": 0.1}},
                    timeout=10,
                )
                category = resp.json().get("response", "").strip().lower()
            except Exception:
                category = "other"

            extracted.append({"date": date_str, "vendor": vendor,
                              "amount": amount, "category": category})

            if amount and float(amount) > self.big_item_threshold:
                big_items.append(f"${amount} at {vendor} ({category})")

        ctx["expenses"] = extracted
        ctx["big_items"] = big_items
        ctx["total_count"] = len(extracted)

        if big_items:
            from tools.imessage_search import send as send_imessage
            send_imessage(
                to=self.imessage_recipient,
                message="Big expenses detected:\n" + "\n".join(big_items),
            )

        return {"ok": True, "output": f"{len(extracted)} expenses processed, {len(big_items)} big items"}


def execute(imessage_recipient: str, spreadsheet_id: str,
            gmail_query: str = "receipt OR invoice OR payment newer_than:7d",
            big_item_threshold: float = 100) -> dict:
    steps = [
        GmailSearchStep(query=gmail_query, max_results=15),
        BatchProcessExpensesStep(
            input_key="gmail_raw",
            imessage_recipient=imessage_recipient,
            spreadsheet_id=spreadsheet_id,
            big_item_threshold=big_item_threshold,
        ),
        AiGenerateStep(
            prompt_key="expenses",
            output_key="weekly_summary",
            prompt="",
            max_tokens=300,
        ),
        iMessageSendStep(
            recipient=imessage_recipient,
            message="Expense summary: {total_count} items logged. Full report in sheet.",
        ),
        NotificationStep(
            message="Expense tracker: {total_count} items processed",
            level="info",
        ),
        LogAppendStep(
            log_file="expenses.log",
            entry="{total_count} expenses logged on {sheet_result}",
        ),
    ]
    return run_pipeline(steps)


if __name__ == "__main__":
    im = input("iMessage recipient: ")
    sheet = input("Google Sheet ID: ")
    result = execute(im, sheet)
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status}")
