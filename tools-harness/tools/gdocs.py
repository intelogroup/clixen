"""
Google Docs tool — list user's documents.

Usage:
    from tools.gdocs import list_google_docs, read_google_doc

GOOGLE_CREDENTIALS_PATH and GOOGLE_TOKEN_PATH must be set in .env
"""

import os
from typing import Optional

LIST_DOCS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_google_docs",
        "description": (
            "List all Google Docs the user has access to. "
            "Use this to find a document when setting up workflows."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

READ_DOC_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_google_doc",
        "description": "Read content from a Google Doc. Returns plain text.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The document ID (from the URL)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max characters to return, default 5000",
                    "default": 5000,
                },
            },
            "required": ["document_id"],
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

    creds = Credentials.from_authorized_user_file(token_path)
    return creds


def list_google_docs() -> str:
    """List all Google Docs."""
    try:
        creds = _get_creds()
        from googleapiclient.discovery import build

        service = build("docs", "v1", credentials=creds)
        result = service.documents().list().execute()
        docs = result.get("documents", [])

        if not docs:
            return "No Google Docs found."

        lines = [f"You have {len(docs)} Google Docs:"]
        for i, doc in enumerate(docs[:20], 1):
            doc_id = doc.get("documentId", "")
            name = doc.get("name", "Untitled")
            lines.append(f"  {i}. {name} (ID: {doc_id})")

        if len(docs) > 20:
            lines.append(f"  ... and {len(docs) - 20} more")

        return "\n".join(lines)
    except Exception as e:
        return f"[gdocs error] {e}"


def read_google_doc(document_id: str, limit: int = 5000) -> str:
    """Read content from a Google Doc."""
    try:
        creds = _get_creds()
        from googleapiclient.discovery import build

        service = build("docs", "v1", credentials=creds)
        doc = service.documents().get(documentId=document_id).execute()

        # Extract text content
        text = ""
        for elem in doc.get("body", {}).get("content", []):
            if "paragraph" in elem:
                for prop in elem["paragraph"].get("elements", []):
                    if "textRun" in prop:
                        text += prop["textRun"].get("content", "")

        if not text:
            return "Empty document"

        # Truncate if needed
        if len(text) > limit:
            text = text[:limit] + f"\n... (truncated, {len(text)} total chars)"

        return f"Doc: {doc.get('title', 'Untitled')}\n\n{text}"
    except Exception as e:
        return f"[gdocs error] {e}"


if __name__ == "__main__":
    print(list_google_docs())
