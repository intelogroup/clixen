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

LIST_CONTACTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_whatsapp_contacts",
        "description": "List contacts known to the connected WhatsApp bridge. Returns names, WhatsApp JIDs, and last-seen timestamps, without message contents.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}

FETCH_HISTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fetch_whatsapp_history",
        "description": (
            "Live on-demand fetch: asks the user's own linked phone for a contact's recent "
            "WhatsApp messages and waits for them to land, then returns the freshest message "
            "for that thread. Use this when the local archive (whatsapp_search / "
            "whatsapp_recent_chats) looks stale or missing for a specific contact — those tools "
            "only read what's already archived, this one refreshes it first. Requires the bridge "
            "to be connected (WhatsApp Web session active)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "contact": {
                    "type": "string",
                    "description": "Contact name, phone number, or JID to refresh. Required unless jid is given.",
                },
                "jid": {
                    "type": "string",
                    "description": "Exact WhatsApp JID to refresh, if already known. Alternative to contact.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many recent messages to request from the phone (1-50). Default 20.",
                    "default": 20,
                },
            },
            "required": [],
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


def fetch_history(contact: str = "", jid: str = "", limit: int = 20) -> str:
    """Ask the linked phone for fresh history on a contact, then report the latest message."""
    if not contact and not jid:
        return "[whatsapp] fetch_history needs a contact name or jid."

    payload = json.dumps({"contact": contact, "jid": jid, "limit": limit}).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{_BRIDGE_URL}/fetchHistory",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Bridge polls the phone's response internally (up to ~5s); give it room.
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            error = body.get("error", str(e))
        except Exception:
            error = str(e)
        if e.code == 503:
            return "[whatsapp] Bridge not connected to WhatsApp — can't fetch live history"
        if e.code == 404:
            return f"[whatsapp] {error}"
        log.error("whatsapp fetch_history failed: %s", error)
        return f"[whatsapp] fetch_history failed: {error}"
    except Exception as e:
        log.error("whatsapp fetch_history failed: %s", e, exc_info=True)
        return f"[whatsapp] fetch_history failed: {e}"

    from tools.whatsapp_search import recent_chats

    target = data.get("jid") or jid or contact
    fresh = recent_chats(1, contact=contact or target)
    fetched = data.get("fetched", 0)
    if fetched == 0:
        return (
            f"[whatsapp] Requested history for {target} but the phone returned nothing new "
            f"(may already be up to date, or the contact has no recent activity). "
            f"Archive shows:\n{fresh}"
        )
    return f"Refreshed {fetched} message(s) for {target}.\n{fresh}"


def list_contacts() -> str:
    """Return the contacts currently known by the WhatsApp bridge."""
    try:
        with urllib.request.urlopen(f"{_BRIDGE_URL}/contacts", timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return json.dumps(data, ensure_ascii=False)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            error = body.get("error", str(e))
        except Exception:
            error = str(e)
        return f"[whatsapp] contacts failed: {error}"
    except Exception as e:
        return f"[whatsapp] contacts failed: {e}"
