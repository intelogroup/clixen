"""
iMessage Digest — Gmail query → iMessage summary.

Use case: New email matching query → iMessage digest to a phone number

Usage:
    from workflows.imessage_digest import create_imessage_digest
    create_imessage_digest(recipient="+1234567890", query="from:boss", hour=18)
"""

from tools.create_watcher import create_watcher

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_imessage_digest",
        "description": (
            "Create an iMessage digest: periodically checks Gmail for new emails "
            "matching a query and sends matching subject lines via iMessage. "
            "Use when: user wants email alerts pushed to their phone as texts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {
                    "type": "string",
                    "description": "iMessage recipient — phone number (+1...) or Apple ID email",
                },
                "query": {
                    "type": "string",
                    "description": "Gmail search query, e.g. 'from:boss' or 'is:unread'",
                },
                "poll_interval_seconds": {
                    "type": "integer",
                    "description": "Poll interval in seconds, default 300 (5 min)",
                    "default": 300,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max emails to check per poll, default 10",
                    "default": 10,
                },
                "message_template": {
                    "type": "string",
                    "description": "Digest template: {sender} and {subject} available",
                    "default": "New email from {sender}: {subject}",
                },
            },
            "required": ["recipient", "query"],
        },
    },
}


def create_imessage_digest(
    recipient: str,
    query: str,
    poll_interval_seconds: int = 300,
    max_results: int = 10,
    message_template: str = None,
) -> str:
    """Create an iMessage digest watcher."""
    trigger_config = {
        "query": query,
        "poll_interval_seconds": poll_interval_seconds,
        "max_results": max_results,
    }
    msg = message_template or "New email from {sender}: {subject}"
    action_config = {
        "recipient": recipient,
        "message": msg,
        "query": query,
        "max_results": max_results,
        "senders": [],
    }
    return create_watcher(
        name=f"iMessage Digest: {query}",
        trigger_type="schedule",
        trigger_config=trigger_config,
        action_type="imessage",
        action_config=action_config,
        schedule={"interval_seconds": poll_interval_seconds},
    )


def run_manual():
    recipient = input("Recipient phone/email: ") or "+1234567890"
    q = input("Gmail query (e.g. from:boss): ") or "from:me"
    result = create_imessage_digest(recipient=recipient, query=q)
    print(result)
    return result


if __name__ == "__main__":
    run_manual()
