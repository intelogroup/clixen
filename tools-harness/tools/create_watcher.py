"""
Create watcher tool — creates trigger→action automations.

API: POST http://localhost:9234/watchers

Use this when the user wants:
- notifications on a schedule (reminders, digests)
- webhook triggers from external services
- monitoring Google Sheets for new rows
- file/folder change alerts
"""

import requests

CREATE_WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_watcher",
        "description": (
            "Create a watcher that monitors a trigger and sends an action automatically. "
            "Use this when the user wants recurring notifications, webhooks, or file monitoring. "
            "The watcher runs in the background and sends Telegram/nothing to you without further action."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "A short descriptive name for this watcher, e.g. 'Daily reminder' or 'Order notifications'",
                },
                "trigger_type": {
                    "type": "string",
                    "enum": ["webhook", "schedule", "google_sheet", "file_change", "email"],
                    "description": (
                        "What triggers the action: "
                        "'webhook' = external HTTP POST to /webhook/{id} triggers it "
                        "'schedule' = runs on a timer (set interval_seconds in schedule) "
                        "'google_sheet' = polls a Google Sheet for new rows "
                        "'file_change' = watches a file/folder for changes "
                        "'email' = polls Gmail for new emails"
                    ),
                },
                "trigger_config": {
                    "type": "object",
                    "description": "Configuration for the trigger, varies by trigger_type",
                    "properties": {
                        "spreadsheet_id": {
                            "type": "string",
                            "description": "Google Sheets ID from the URL (for google_sheet trigger)",
                        },
                        "sheet_name": {
                            "type": "string",
                            "description": "Sheet tab name, default 'Sheet1' (for google_sheet)",
                        },
                        "watch_column": {
                            "type": "string",
                            "description": "Column letter to watch for new data, default 'A' (for google_sheet)",
                        },
                        "poll_interval_seconds": {
                            "type": "integer",
                            "description": "How often to check for changes, default 30 seconds",
                        },
                        "path": {
                            "type": "string",
                            "description": "File or folder path to watch (for file_change trigger)",
                        },
                        "secret": {
                            "type": "string",
                            "description": "Optional secret for webhook verification",
                        },
                        "query": {
                            "type": "string",
                            "description": "Gmail search query (for email trigger, e.g. 'is:unread has:attachment')",
                        },
                        "from": {
                            "type": "string",
                            "description": "Filter by sender email address (for email trigger)",
                        },
                    },
                },
                "action_type": {
                    "type": "string",
                    "enum": ["telegram", "notification", "http_webhook", "email", "imessage"],
                    "description": "What action happens when triggered",
                },
                "action_config": {
                    "type": "object",
                    "description": "Configuration for the action",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Message to send. Use {col} for spreadsheet values, e.g. 'New row: {A}, {B}'",
                        },
                        "url": {
                            "type": "string",
                            "description": "URL to POST to (for http_webhook action)",
                        },
                        "method": {
                            "type": "string",
                            "description": "HTTP method, default 'POST' (for http_webhook)",
                        },
                        "to": {
                            "type": "string",
                            "description": "Email recipient (for email action)",
                        },
                        "recipient": {
                            "type": "string",
                            "description": "iMessage recipient phone/email (for imessage action)",
                        },
                    },
                },
                "schedule": {
                    "type": "object",
                    "description": "Schedule for recurring watchers (for schedule trigger_type)",
                    "properties": {
                        "interval_seconds": {
                            "type": "integer",
                            "description": "Seconds between runs: 60=1min, 3600=1hour, 86400=1day",
                        },
                    },
                },
            },
            "required": ["name", "trigger_type", "action_type"],
        },
    },
}


# Additional watcher management tools

DELETE_WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_watcher",
        "description": "Delete a watcher by its ID. Use list_watchers first to find the ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "watcher_id": {"type": "string", "description": "The watcher ID to delete"},
            },
            "required": ["watcher_id"],
        },
    },
}

PAUSE_WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "pause_watcher",
        "description": "Pause a watcher temporarily. Use resume_watcher to restart it.",
        "parameters": {
            "type": "object",
            "properties": {
                "watcher_id": {"type": "string", "description": "The watcher ID to pause"},
            },
            "required": ["watcher_id"],
        },
    },
}

RESUME_WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "resume_watcher",
        "description": "Resume a paused watcher.",
        "parameters": {
            "type": "object",
            "properties": {
                "watcher_id": {"type": "string", "description": "The watcher ID to resume"},
            },
            "required": ["watcher_id"],
        },
    },
}

LIST_WATCHERS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_watchers",
        "description": "List all watchers. Shows name, ID, trigger_type, action_type, and status.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "deleted", ""],
                    "description": "Filter by status: 'active', 'paused', or leave empty for all",
                },
            },
        },
    },
}


def create_watcher(
    name: str,
    trigger_type: str,
    trigger_config: dict = None,
    action_type: str = "telegram",
    action_config: dict = None,
    schedule: dict = None,
) -> str:
    """Create a watcher automation."""
    payload = {
        "name": name,
        "trigger_type": trigger_type,
        "trigger_config": trigger_config or {},
        "action_type": action_type,
        "action_config": action_config or {},
        "schedule": schedule,
    }
    try:
        resp = requests.post("http://localhost:9234/watchers", json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        return f"Watcher created: id={result.get('id')}, status={result.get('status')}"
    except Exception as e:
        return f"[watcher error] Failed to create watcher: {e}"


def delete_watcher(watcher_id: str) -> str:
    """Delete a watcher by ID."""
    try:
        resp = requests.delete(f"http://localhost:9234/watchers/{watcher_id}", timeout=10)
        resp.raise_for_status()
        return f"Watcher {watcher_id} deleted"
    except Exception as e:
        return f"[watcher error] Failed to delete: {e}"


def pause_watcher(watcher_id: str) -> str:
    """Pause a watcher."""
    try:
        resp = requests.post(f"http://localhost:9234/watchers/{watcher_id}/pause", timeout=10)
        resp.raise_for_status()
        return f"Watcher {watcher_id} paused"
    except Exception as e:
        return f"[watcher error] Failed to pause: {e}"


def resume_watcher(watcher_id: str) -> str:
    """Resume a paused watcher."""
    try:
        resp = requests.post(f"http://localhost:9234/watchers/{watcher_id}/resume", timeout=10)
        resp.raise_for_status()
        return f"Watcher {watcher_id} resumed"
    except Exception as e:
        return f"[watcher error] Failed to resume: {e}"


def list_watchers(status: str = "") -> str:
    """List all watchers."""
    try:
        url = "http://localhost:9234/watchers"
        if status:
            url += f"?status={status}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        watchers = result.get("watchers", [])
        if not watchers:
            return "No watchers found"
        lines = ["Watchers:"]
        for w in watchers:
            lines.append(
                f"  - {w.get('name')}: id={w.get('id')}, trigger={w.get('trigger_type')}, "
                f"action={w.get('action_type')}, status={w.get('status')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"[watcher error] Failed to list: {e}"
