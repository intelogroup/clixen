"""
Invoice Tracker — Gmail attachment → save folder + Telegram notification.

Use case: Invoice email arrives → auto-save PDF to folder + notify

Note: This uses file_polling since Gmail→file is complex.
      Alternative: Gmail filter → auto-forward to folder → file trigger

Usage:
    from workflows.invoice_tracker import create_invoice_tracker
    create_invoice_tracker(save_folder="/path/to/invoices")
"""

from tools.create_watcher import create_watcher

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_invoice_tracker",
        "description": (
            "Create an invoice tracker: watches a folder for new PDF files "
            "and sends Telegram notifications. "
            "Use when: user wants to be notified when invoices are received."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "watch_folder": {
                    "type": "string",
                    "description": "Full path to folder to watch, e.g. ~/Documents/Invoices",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "File pattern to match, default '*.pdf'",
                    "default": "*.pdf",
                },
                "telegram_message": {
                    "type": "string",
                    "description": "Message template, use {filename} for file name",
                    "default": "📄 New invoice: {filename}",
                },
                "poll_interval_seconds": {
                    "type": "integer",
                    "description": "How often to check, default 60 seconds",
                    "default": 60,
                },
            },
            "required": ["watch_folder"],
        },
    },
}


def create_invoice_tracker(
    watch_folder: str,
    file_pattern: str = "*.pdf",
    telegram_message: str = "📄 New invoice: {filename}",
    poll_interval_seconds: int = 60,
) -> str:
    """Create an invoice tracker watcher."""
    import os

    folder = os.path.expanduser(watch_folder)
    msg_template = telegram_message or "📄 New invoice received!"

    trigger_config = {
        "path": folder,
        "pattern": file_pattern,
        "poll_interval_seconds": poll_interval_seconds,
    }

    action_config = {"message": msg_template}

    return create_watcher(
        name=f"Invoice Tracker: {folder}",
        trigger_type="file_change",
        trigger_config=trigger_config,
        action_type="telegram",
        action_config=action_config,
        schedule={"interval_seconds": poll_interval_seconds},
    )


def run_manual():
    """Test runner for manual testing."""
    folder = input("Enter folder path to watch: ") or "~/Documents/Invoices"
    result = create_invoice_tracker(
        watch_folder=folder,
        telegram_message="📄 New invoice: {filename}",
    )
    print(result)
    return result


if __name__ == "__main__":
    run_manual()
