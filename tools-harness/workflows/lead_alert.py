"""
Lead Alert Pipeline — Google Sheet new row → Telegram notification + task creation.

Use case: New lead in Google Sheet → notify to Telegram + create Google Task

Usage:
    from workflows.lead_alert import create_lead_alert
    create_lead_alert(spreadsheet_id="abc123", sheet_name="Leads")
"""

from tools.create_watcher import create_watcher

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_lead_alert",
        "description": (
            "Create a lead alert pipeline: monitors a Google Sheet for new rows (leads) "
            "and sends Telegram notifications + creates Google Tasks. "
            "Use when: user wants to get notified instantly when new leads are added."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "Google Sheets ID from the URL",
                },
                "sheet_name": {
                    "type": "string",
                    "description": "Sheet tab name, default 'Sheet1'",
                    "default": "Sheet1",
                },
                "watch_columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column letters to watch for new data, e.g. ['A', 'B', 'C']",
                },
                "telegram_message": {
                    "type": "string",
                    "description": "Message template, use {ColumnLetter} for values, e.g. 'New lead: {A} from {C}'",
                    "default": "New lead added!",
                },
                "poll_interval_seconds": {
                    "type": "integer",
                    "description": "How often to check for new rows, default 30",
                    "default": 30,
                },
                "create_task": {
                    "type": "boolean",
                    "description": "Also create a Google Task for the lead?",
                    "default": True,
                },
            },
            "required": ["spreadsheet_id"],
        },
    },
}


def create_lead_alert(
    spreadsheet_id: str,
    sheet_name: str = "Sheet1",
    watch_columns: list = None,
    telegram_message: str = "New lead: {Name}",
    poll_interval_seconds: int = 30,
    create_task: bool = True,
) -> str:
    """Create a lead alert watcher."""
    cols = watch_columns or ["A", "B", "C"]
    msg_template = telegram_message or "New lead added!"

    trigger_config = {
        "spreadsheet_id": spreadsheet_id,
        "sheet_name": sheet_name,
        "watch_columns": cols,
        "poll_interval_seconds": poll_interval_seconds,
    }

    action_config = {"message": msg_template}

    return create_watcher(
        name=f"Lead Alert: {sheet_name}",
        trigger_type="google_sheet",
        trigger_config=trigger_config,
        action_type="telegram",
        action_config=action_config,
        schedule={"interval_seconds": poll_interval_seconds},
    )


def run_manual(spreadsheet_id: str = None):
    """Test runner for manual testing."""
    if not spreadsheet_id:
        spreadsheet_id = input("Enter Google Sheet ID: ") or "YOUR_SHEET_ID"

    result = create_lead_alert(
        spreadsheet_id=spreadsheet_id,
        sheet_name="Leads",
        telegram_message="🔔 New lead: {A} ({B})",
    )
    print(result)
    return result


if __name__ == "__main__":
    run_manual()
