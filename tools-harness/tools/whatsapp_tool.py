"""
WhatsApp messaging tool — sends messages through the WhatsApp bridge.

Usage:
    from tools.whatsapp_tool import execute as send_whatsapp
    result = send_whatsapp("14155552671", "Hello from G4L")

Bridge must be running on localhost:9235.
"""

from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger("whatsapp_tool")

_BRIDGE_URL = "http://127.0.0.1:9235"
_TIMEOUT = 15

SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_whatsapp",
        "description": (
            "Send a WhatsApp message to a phone number. The number must be in international format "
            "with country code (e.g. 14155552671 for US). The recipient must be an existing WhatsApp contact "
            "or have an active chat thread. Messages are rate-limited to 20 per minute per contact."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": (
                        "Phone number in international format with country code, appended with @s.whatsapp.net. "
                        "Example: '14155552671@s.whatsapp.net' for US number +1 (415) 555-2671. "
                        "If user only gives a phone number without suffix, append @s.whatsapp.net automatically."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "Text message to send (max 4096 characters)",
                },
            },
            "required": ["to", "message"],
        },
    },
}


def execute(to: str, message: str) -> str:
    """Send a WhatsApp message via the bridge."""
    if not to.endswith("@s.whatsapp.net"):
        to = f"{to}@s.whatsapp.net"

    payload = json.dumps({"to": to, "message": message}).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{_BRIDGE_URL}/send",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            status = data.get("status", "unknown")
            if status == "sent":
                return f"WhatsApp message sent to {to}"
            return f"[whatsapp] {data.get('error', 'unknown error')}"

    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            error = body.get("error", str(e))
        except Exception:
            error = str(e)
        if e.code == 503:
            return "[whatsapp] Bridge not connected to WhatsApp — authenticate first"
        if e.code == 429:
            return "[whatsapp] Rate limit exceeded — wait and retry"
        log.error("whatsapp send failed: %s", error)
        return f"[whatsapp] send failed: {error}"

    except Exception as e:
        log.error("whatsapp send failed: %s", e, exc_info=True)
        return f"[whatsapp] send failed: {e}"
