"""
Google Sheets tool — list user's spreadsheets.

Usage:
    from tools.gsheets import list_sheets, list_sheet_data

GOOGLE_CREDENTIALS_PATH and GOOGLE_TOKEN_PATH must be set in .env
"""

import os
import logging
from typing import Optional

logger = logging.getLogger("gsheets")

LIST_SHEETS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_google_sheets",
        "description": (
            "List all Google Sheets the user has access to. "
            "Use this to find a sheet ID when setting up workflows."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

READ_SHEET_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_google_sheet",
        "description": "Read data from a specific Google Sheet. Returns rows as JSON.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "The spreadsheet ID (from the URL, between /d/ and /edit)",
                },
                "sheet_name": {
                    "type": "string",
                    "description": "Sheet tab name, default 'Sheet1'",
                    "default": "Sheet1",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return, default 100",
                    "default": 100,
                },
            },
            "required": ["spreadsheet_id"],
        },
    },
}


def _get_creds():
    """Get Google credentials from env."""
    from pathlib import Path

    creds_path = os.environ.get(
        "GOOGLE_CREDENTIALS_PATH",
        str(Path.home() / "Downloads/credentials.json"),
    )
    token_path = os.environ.get(
        "GOOGLE_TOKEN_PATH",
        str(Path.home() / ".config/google-mcp/token.json"),
    )

    if not os.path.exists(creds_path):
        raise ValueError(f"credentials.json not found: {creds_path}")

    from google.oauth2.credentials import Credentials
    import json

    # Load token file
    with open(token_path) as f:
        token_data = json.load(f)

    creds = Credentials.from_authorized_user_info(token_data)
    return creds


def list_google_sheets() -> str:
    """List all Google Sheets."""
    try:
        creds = _get_creds()
        from googleapiclient.discovery import build

        service = build("sheets", "v4", credentials=creds)
        result = service.spreadsheets().list().execute()
        sheets = result.get("spreadsheets", [])

        if not sheets:
            return "No Google Sheets found."

        lines = [f"You have {len(sheets)} Google Sheets:"]
        for i, sheet in enumerate(sheets[:20], 1):
            sheet_id = sheet.get("spreadsheetId", "")
            name = sheet.get("properties", {}).get("title", "Untitled")
            lines.append(f"  {i}. {name} (ID: {sheet_id})")

        if len(sheets) > 20:
            lines.append(f"  ... and {len(sheets) - 20} more")

        return "\n".join(lines)
    except Exception as e:
        return f"[gsheets error] {e}"


def read_google_sheet(
    spreadsheet_id: str,
    sheet_name: str = "Sheet1",
    limit: int = 100,
) -> str:
    """Read data from a Google Sheet."""
    try:
        creds = _get_creds()
        from googleapiclient.discovery import build

        service = build("sheets", "v4", credentials=creds)

        # Get sheet ID by name
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets = meta.get("sheets", [])
        sheet_id = None
        for s in sheets:
            props = s.get("properties", {})
            if props.get("title") == sheet_name:
                sheet_id = props.get("sheetId")
                break

        if sheet_id is None:
            return f"[error] Sheet '{sheet_name}' not found"

        # Read data
        range_name = f"{sheet_name}!A1:{limit}"
        result = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=range_name,
            )
            .execute()
        )

        values = result.get("values", [])
        if not values:
            return "Empty sheet"

        # Format as table
        lines = []
        for i, row in enumerate(values[:20]):
            lines.append(" | ".join(str(c) for c in row))

        output = f"Sheet: {sheet_name} ({len(values)} rows)\n"
        output += "\n".join(lines)

        if len(values) > 20:
            output += f"\n... and {len(values) - 20} more rows"

        return output
    except Exception as e:
        return f"[gsheets error] {e}"


if __name__ == "__main__":
    print(list_google_sheets())
