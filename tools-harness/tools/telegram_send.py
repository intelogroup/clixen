"""
Telegram send tool — sends a message to the owner's Telegram chat.

Required .env keys:
    TELEGRAM_BOT_TOKEN      — bot token from @BotFather
    TELEGRAM_OWNER_CHAT_ID  — numeric chat ID of the owner
    TELEGRAM_BASE_URL       — API base URL (e.g. Cloudflare proxy or https://api.telegram.org/bot)
"""
import os
import json
import time

SEND_TELEGRAM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_telegram",
        "description": (
            "Send a text message to the owner via Telegram. "
            "Use this to deliver summaries, alerts, or notifications directly to the user's phone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The text message to send. Keep it concise and plain text (no HTML or Markdown).",
                },
            },
            "required": ["message"],
        },
    },
}


def send_telegram(message: str, chat_id: str = None) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cid = chat_id or os.environ.get("TELEGRAM_OWNER_CHAT_ID", "")
    base_url = os.environ.get("TELEGRAM_BASE_URL", "https://api.telegram.org/bot")

    if not token or not cid:
        return "[telegram error] TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_CHAT_ID not set in .env"

    url = f"{base_url}{token}/sendMessage"

    import requests

    last_exc = None
    for attempt in range(2):  # ponytail: one retry, covers transient wifi blips like Errno 51
        try:
            resp = requests.post(url, json={"chat_id": cid, "text": message}, timeout=10)
            result = resp.json()
            if result.get("ok"):
                return f"Telegram message sent (message_id={result['result']['message_id']})"
            return f"[telegram error] API returned: {result}"
        except Exception as e:
            last_exc = e
            if attempt == 0:
                time.sleep(2)
    return f"[telegram error] send_telegram failed: {last_exc}"
