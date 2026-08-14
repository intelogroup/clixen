"""Document-agent tool for bounded, user-visible working notes."""

from __future__ import annotations

from tools.document_workspace import add_note, scratchpad_block

SCHEMA = {
    "type": "function",
    "function": {
        "name": "document_scratchpad",
        "description": (
            "Save or read a short, user-visible working note for this document session. "
            "Use for extracted facts, decisions, and unresolved items—not hidden reasoning. "
            "Notes are bounded and expire automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Current document chat/session id"},
                "workspace": {"type": "string", "description": "Current local workspace root"},
                "note": {"type": "string", "description": "Short note; omit to read existing notes."},
            },
            "required": ["session_id", "workspace"],
        },
    },
}


def execute(session_id: str, workspace: str, note: str = "") -> str:
    if note.strip():
        return add_note(session_id, note, workspace)
    block = scratchpad_block(session_id, workspace)
    return block or "Scratchpad is empty."
