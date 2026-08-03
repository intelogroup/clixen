"""
Google Docs tool — full CRUD operations.

Usage:
    from tools.gdocs_tool import list_google_docs, read_google_doc
    from tools.gdocs_tool import create_google_doc, update_google_doc, append_google_doc

Requires Google OAuth (uses same token as Gmail/Calendar/Tasks).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("gdocs_tool")

LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_google_docs",
        "description": "List all Google Docs the user has access to. Returns title and document ID.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_google_doc",
        "description": "Read the plain text content of a Google Doc by its document ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The document ID from the URL (between /d/ and /edit)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max characters to return (default 5000)",
                    "default": 5000,
                },
            },
            "required": ["document_id"],
        },
    },
}

CREATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_google_doc",
        "description": (
            "Create a new Google Doc with a title and optional content. "
            "Returns the document ID and URL. Use this to save research, reports, or notes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title for the new document",
                },
                "content": {
                    "type": "string",
                    "description": "Initial content (plain text) for the document. Use newlines for paragraphs.",
                },
            },
            "required": ["title"],
        },
    },
}

APPEND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "append_google_doc",
        "description": "Append text to the end of an existing Google Doc.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The document ID from the URL",
                },
                "content": {
                    "type": "string",
                    "description": "Text to append (use \\n for new paragraphs)",
                },
            },
            "required": ["document_id", "content"],
        },
    },
}

UPDATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_google_doc_title",
        "description": "Change the title of an existing Google Doc.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The document ID from the URL",
                },
                "title": {
                    "type": "string",
                    "description": "New title for the document",
                },
            },
            "required": ["document_id", "title"],
        },
    },
}

SCHEMAS = [LIST_SCHEMA, READ_SCHEMA, CREATE_SCHEMA, APPEND_SCHEMA, UPDATE_SCHEMA]


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


def _get_docs_service():
    from googleapiclient.discovery import build
    return build("docs", "v1", credentials=_get_creds())


def _get_drive_service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_get_creds())


def list_google_docs(args: dict) -> str:
    try:
        drive = _get_drive_service()
        results = (
            drive.files()
            .list(
                q="mimeType='application/vnd.google-apps.document'",
                pageSize=50,
                fields="files(id, name, modifiedTime)",
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        docs = results.get("files", [])
        if not docs:
            return "No Google Docs found."

        lines = [f"You have {len(docs)} Google Docs:"]
        for i, doc in enumerate(docs[:30], 1):
            name = doc.get("name", "Untitled")
            doc_id = doc.get("id", "")
            modified = doc.get("modifiedTime", "")[:10]
            lines.append(f"  {i}. {name} (ID: {doc_id}, modified: {modified})")
        if len(docs) > 30:
            lines.append(f"  ... and {len(docs) - 30} more")
        return "\n".join(lines)
    except Exception as e:
        return f"[gdocs error] {e}"


def read_google_doc(args: dict) -> str:
    doc_id = args["document_id"]
    limit = args.get("limit", 5000)
    try:
        service = _get_docs_service()
        doc = service.documents().get(documentId=doc_id).execute()

        text = ""
        for elem in doc.get("body", {}).get("content", []):
            if "paragraph" in elem:
                for prop in elem["paragraph"].get("elements", []):
                    if "textRun" in prop:
                        text += prop["textRun"].get("content", "")

        if not text:
            return "Empty document"

        if len(text) > limit:
            text = text[:limit] + f"\n... (truncated, {len(text)} total chars)"

        title = doc.get("title", "Untitled")
        return f"Doc: {title}\n\n{text}"
    except Exception as e:
        return f"[gdocs error] {e}"


def create_google_doc(args: dict) -> str:
    title = args["title"]
    content = args.get("content", "")
    try:
        service = _get_docs_service()
        doc = service.documents().create(body={"title": title}).execute()
        doc_id = doc.get("documentId", "")

        if content:
            # Insert content via batchUpdate
            requests = [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": content,
                    }
                }
            ]
            service.documents().batchUpdate(
                documentId=doc_id, body={"requests": requests}
            ).execute()

        url = f"https://docs.google.com/document/d/{doc_id}/edit"
        return f"Created Google Doc: '{title}'\nID: {doc_id}\nURL: {url}"
    except Exception as e:
        return f"[gdocs error] {e}"


def append_google_doc(args: dict) -> str:
    doc_id = args["document_id"]
    content = args["content"]
    try:
        service = _get_docs_service()

        # Find end index
        doc = service.documents().get(documentId=doc_id).execute()
        end_index = 1
        for elem in doc.get("body", {}).get("content", []):
            ei = elem.get("endIndex", 0)
            if ei > end_index:
                end_index = ei

        requests = [
            {
                "insertText": {
                    "location": {"index": end_index - 1},
                    "text": f"\n{content}",
                }
            }
        ]
        service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()

        return f"Appended to doc: {doc_id}"
    except Exception as e:
        return f"[gdocs error] {e}"


def update_google_doc_title(args: dict) -> str:
    doc_id = args["document_id"]
    title = args["title"]
    try:
        drive = _get_drive_service()
        drive.files().update(fileId=doc_id, body={"name": title}).execute()
        return f"Renamed doc to: '{title}'"
    except Exception as e:
        return f"[gdocs error] {e}"
