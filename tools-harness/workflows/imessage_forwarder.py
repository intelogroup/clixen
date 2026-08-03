"""
iMessage Forwarder — Webhook → iMessage notification.

Use case: External webhook POST → iMessage sent to a phone number

Usage:
    from workflows.imessage_forwarder import create_imessage_forwarder
    create_imessage_forwarder(recipient="+1234567890", secret="abc")
"""

from tools.create_watcher import create_watcher

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_imessage_forwarder",
        "description": (
            "Create an iMessage forwarder: external service sends a webhook POST "
            "to clixen and it forwards the payload as an iMessage. "
            "Use when: user wants external service alerts sent to their phone as texts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {
                    "type": "string",
                    "description": "iMessage recipient — phone number (+1...) or Apple ID email",
                },
                "secret": {
                    "type": "string",
                    "description": "Webhook secret for verification",
                },
                "custom_message": {
                    "type": "string",
                    "description": "Message template, use {payload} for body, default 'Webhook alert: {payload}'",
                    "default": "Webhook alert: {payload}",
                },
            },
            "required": ["recipient", "secret"],
        },
    },
}


def create_imessage_forwarder(
    recipient: str,
    secret: str,
    custom_message: str = None,
) -> str:
    """Create an iMessage webhook forwarder."""
    msg = custom_message or "Webhook alert: {payload}"
    trigger_config = {
        "secret": secret,
    }
    action_config = {
        "recipient": recipient,
        "message": msg,
    }
    return create_watcher(
        name=f"iMessage Forwarder (webhook)",
        trigger_type="webhook",
        trigger_config=trigger_config,
        action_type="imessage",
        action_config=action_config,
    )


def run_manual():
    recipient = input("Recipient phone/email: ") or "+1234567890"
    secret = input("Webhook secret: ") or "test-secret"
    result = create_imessage_forwarder(recipient=recipient, secret=secret)
    print(result)
    return result


if __name__ == "__main__":
    run_manual()
