"""
Google Sheets tool — full CRUD operations.

Usage:
    from tools.gsheets_tool import (
        list_google_sheets, read_google_sheet,
        create_google_sheet, append_google_sheet, update_cell,
    )

Requires Google OAuth (uses same token as Gmail/Calendar/Tasks).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("gsheets_tool")

LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_google_sheets",
        "description": "List all Google Sheets the user has access to. Returns title and spreadsheet ID.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_google_sheet",
        "description": "Read data from a specific sheet tab in a Google Sheets spreadsheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "The spreadsheet ID from the URL (between /d/ and /edit)",
                },
                "sheet_name": {
                    "type": "string",
                    "description": "Sheet tab name (default: 'Sheet1')",
                    "default": "Sheet1",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (default 100)",
                    "default": 100,
                },
            },
            "required": ["spreadsheet_id"],
        },
    },
}

CREATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_google_sheet",
        "description": (
            "Create a new Google Sheets spreadsheet with optional headers and data rows. "
            "Headers become column names. Data rows are arrays of values. "
            "Returns the spreadsheet ID and URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title for the new spreadsheet",
                },
                "headers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional column header names for the first row",
                },
                "rows": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "description": "Optional data rows (each row is an array of values)",
                },
            },
            "required": ["title"],
        },
    },
}

APPEND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "append_google_sheet",
        "description": (
            "Append one or more rows to an existing Google Sheet. "
            "If headers are provided and the sheet is empty, headers are written as the first row. "
            "Each row should have the same number of columns as existing data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "The spreadsheet ID from the URL",
                },
                "sheet_name": {
                    "type": "string",
                    "description": "Sheet tab name (default: 'Sheet1')",
                    "default": "Sheet1",
                },
                "rows": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "description": "Rows to append (each row is an array of string values)",
                },
            },
            "required": ["spreadsheet_id", "rows"],
        },
    },
}

UPDATE_CELL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_google_sheet_cell",
        "description": "Update a specific cell or range in a Google Sheet.",
        "parameters": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "The spreadsheet ID from the URL",
                },
                "sheet_name": {
                    "type": "string",
                    "description": "Sheet tab name (default: 'Sheet1')",
                    "default": "Sheet1",
                },
                "range": {
                    "type": "string",
                    "description": "Cell range in A1 notation (e.g. 'A1', 'B2:C5')",
                },
                "values": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "description": "Values to write (array of rows, each row is an array of cell values)",
                },
            },
            "required": ["spreadsheet_id", "range", "values"],
        },
    },
}

SCHEMAS = [LIST_SCHEMA, READ_SCHEMA, CREATE_SCHEMA, APPEND_SCHEMA, UPDATE_CELL_SCHEMA]


def _get_creds():
    from google.oauth2.credentials import Credentials

    token_path = os.environ.get(
        "GOOGLE_TOKEN_PATH",
        str(Path.home() / ".config/google-mcp/token.json"),
    )
    if not os.path.exists(token_path):
        raise ValueError(f"token.json not found: {token_path} — run auth first")

    creds = Credentials.from_authorized_user_file(token_path)
    return creds


def _get_sheets_service():
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=_get_creds())


def _get_drive_service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_get_creds())


def list_google_sheets(args: dict) -> str:
    try:
        drive = _get_drive_service()
        results = (
            drive.files()
            .list(
                q="mimeType='application/vnd.google-apps.spreadsheet'",
                pageSize=50,
                fields="files(id, name, modifiedTime)",
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        sheets = results.get("files", [])
        if not sheets:
            return "No Google Sheets found."

        lines = [f"You have {len(sheets)} Google Sheets:"]
        for i, s in enumerate(sheets[:30], 1):
            name = s.get("name", "Untitled")
            sid = s.get("id", "")
            modified = s.get("modifiedTime", "")[:10]
            lines.append(f"  {i}. {name} (ID: {sid}, modified: {modified})")
        if len(sheets) > 30:
            lines.append(f"  ... and {len(sheets) - 30} more")
        return "\n".join(lines)
    except Exception as e:
        return f"[gsheets error] {e}"


def read_google_sheet(args: dict) -> str:
    spreadsheet_id = args["spreadsheet_id"]
    sheet_name = args.get("sheet_name", "Sheet1")
    limit = args.get("limit", 100)
    try:
        service = _get_sheets_service()
        range_name = f"{sheet_name}!A1:Z{limit}"
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
        )
        values = result.get("values", [])
        if not values:
            return f"Sheet '{sheet_name}' is empty"

        lines = []
        for i, row in enumerate(values[:50]):
            lines.append(" | ".join(str(c) if c else "" for c in row))

        output = f"Sheet: {sheet_name} ({len(values)} rows total)\n"
        output += "\n".join(lines)
        if len(values) > 50:
            output += f"\n... and {len(values) - 50} more rows"
        return output
    except Exception as e:
        return f"[gsheets error] {e}"


def create_google_sheet(args: dict) -> str:
    title = args["title"]
    headers = args.get("headers", [])
    rows = args.get("rows", [])
    try:
        service = _get_sheets_service()

        spreadsheet = (
            service.spreadsheets()
            .create(body={"properties": {"title": title}})
            .execute()
        )
        sid = spreadsheet.get("spreadsheetId", "")

        # Write headers + data
        all_rows = []
        if headers:
            all_rows.append(headers)
        all_rows.extend(rows)

        if all_rows:
            service.spreadsheets().values().update(
                spreadsheetId=sid,
                range=f"Sheet1!A1",
                body={"values": all_rows},
                valueInputOption="RAW",
            ).execute()

        if headers:
            cols = len(headers)
        elif rows:
            cols = len(rows[0])
        else:
            cols = 0

        url = f"https://docs.google.com/spreadsheets/d/{sid}/edit"
        result = f"Created Google Sheet: '{title}'\nID: {sid}\nURL: {url}"
        if cols:
            result += f"\nColumns: {cols}, Rows: {len(all_rows)}"
        return result
    except Exception as e:
        return f"[gsheets error] {e}"


def append_google_sheet(args: dict) -> str:
    spreadsheet_id = args["spreadsheet_id"]
    sheet_name = args.get("sheet_name", "Sheet1")
    rows = args["rows"]
    try:
        service = _get_sheets_service()

        range_name = f"{sheet_name}!A1:Z1"
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
        )
        existing = result.get("values", [])

        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A:Z",
            body={"values": rows},
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
        ).execute()

        return f"Appended {len(rows)} row(s) to sheet: {sheet_name}"
    except Exception as e:
        return f"[gsheets error] {e}"


def update_google_sheet_cell(args: dict) -> str:
    spreadsheet_id = args["spreadsheet_id"]
    sheet_name = args.get("sheet_name", "Sheet1")
    range_name = args["range"]
    values = args["values"]
    try:
        service = _get_sheets_service()
        full_range = f"{sheet_name}!{range_name}"
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=full_range,
            body={"values": values},
            valueInputOption="RAW",
        ).execute()
        return f"Updated {full_range} in spreadsheet"
    except Exception as e:
        return f"[gsheets error] {e}"
