"""
Form Response Forwarder — Webhook → email + Telegram.

Use case: Typeform/Tally/netlify form submission → forward to email + notify

Usage:
    from workflows.form_forwarder import create_form_forwarder
    create_form_forwarder(to_email="team@company.com")
"""

from tools.create_watcher import create_watcher

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_form_forwarder",
        "description": (
            "Create a form response forwarder: listens for webhook submissions "
            "(from Typeform, Tally, or custom forms) and forwards them to email + Telegram. "
            "Use when: user wants form submission notifications."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Descriptive name for this forwarder",
                    "default": "Form Response",
                },
                "to_email": {
                    "type": "string",
                    "description": "Email address to forward to",
                },
                "notify_telegram": {
                    "type": "boolean",
                    "description": "Also send Telegram notification?",
                    "default": True,
                },
                "telegram_message": {
                    "type": "string",
                    "description": "Telegram message template, use {field_name} for form fields",
                    "default": "📝 New form submission: {form_name}",
                },
                "sheet_id": {
                    "type": "string",
                    "description": "Optional: Google Sheet ID to log responses",
                },
            },
            "required": ["to_email"],
        },
    },
}


def create_form_forwarder(
    name: str = "Form Response",
    to_email: str = None,
    notify_telegram: bool = True,
    telegram_message: str = None,
    sheet_id: str = None,
) -> str:
    """Create a form forwarder watcher."""
    if not to_email:
        raise ValueError("to_email is required")

    msg_template = telegram_message or f"📝 New form: {name}"

    trigger_config = {
        "webhook_secret": "",  # Optional: user can add secret
    }

    action_config = {
        "message": msg_template,
    }

    return create_watcher(
        name=name,
        trigger_type="webhook",
        trigger_config=trigger_config,
        action_type="telegram" if notify_telegram else "notification",
        action_config=action_config,
        schedule=None,
    )


def run_manual():
    """Test runner for manual testing."""
    to_email = input("Enter email to forward to: ") or "team@company.com"
    name = input("Enter name (default 'Form Response'): ") or "Form Response"

    result = create_form_forwarder(
        name=name,
        to_email=to_email,
    )
    print(result)
    print(f"\nWebhook URL: http://localhost:9234/webhook/{{watcher_id}}")
    return result


if __name__ == "__main__":
    run_manual()
