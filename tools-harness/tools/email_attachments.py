"""
Lists files already archived from email attachments under
~/Documents/email-attachments (see jobs/handlers/email_attachment_archive.py),
organized by month/week. Lets the email subagent check what's already been
downloaded before falling back to a live Gmail search — the "email" intent
toolset otherwise has no filesystem visibility at all, which pushed subscription/
receipt questions into the top-level orchestrator's raw shell/OCR tools instead
(confirmed root cause of a 2026-07-07 crash — see CLAUDE.md Known Issues).
"""
from pathlib import Path

ARCHIVE_DIR = Path.home() / "Documents" / "email-attachments"

LIST_EMAIL_ATTACHMENTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_email_attachments",
        "description": (
            "List files already archived from email attachments (invoices, receipts, "
            "images, calendar files, etc.) under ~/Documents/email-attachments, organized "
            "by month and week. Check this before searching Gmail live when the user asks "
            "about past receipts, invoices, or subscriptions — the attachments may already "
            "be downloaded here. Use semantic_file_search for meaning-based search over "
            "their content once you know what's archived."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "month": {
                    "type": "string",
                    "description": "Optional month name to filter (e.g. 'June'). Omit to list all months.",
                    "default": "",
                },
            },
            "required": [],
        },
    },
}


def list_email_attachments(month: str = "") -> str:
    if not ARCHIVE_DIR.exists():
        return "No email-attachments archive found yet."

    month_dirs = [ARCHIVE_DIR / month] if month else sorted(
        d for d in ARCHIVE_DIR.iterdir() if d.is_dir()
    )

    lines = []
    for month_dir in month_dirs:
        if not month_dir.is_dir():
            continue
        for week_dir in sorted(d for d in month_dir.iterdir() if d.is_dir()):
            files = sorted(f.name for f in week_dir.iterdir() if f.is_file())
            if files:
                lines.append(f"{month_dir.name}/{week_dir.name}: " + ", ".join(files))

    if not lines:
        return f"No attachments found{' for ' + month if month else ''}."
    return "\n".join(lines)
