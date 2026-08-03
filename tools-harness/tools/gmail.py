"""
Gmail tools — list, read, and send email via Gmail API.

One-time setup (run interactively before starting the bot):
    python tools/gmail.py --auth

Required .env keys:
    GOOGLE_CLIENT_ID
    GOOGLE_CLIENT_SECRET
    GOOGLE_TOKEN_PATH   (e.g. ~/.config/g4l/gmail_token.json)
"""
import base64
import logging
import re as _re
import os
from pathlib import Path

from tools.google_auth import execute_with_google_auth_retry, get_service

log = logging.getLogger(__name__)

def _resolve_attachment_path(path_str: str) -> Path:
    """LLMs emit /home/user/... (Linux-style) which doesn't exist on macOS —
    fall back to the real home dir, same pattern as form_tools/flat_pdf_tools."""
    p = Path(path_str).expanduser()
    if not p.exists():
        home = os.path.expanduser("~")
        if not str(p).startswith(home) and path_str.startswith("/home/"):
            fallback = Path(home) / "/".join(Path(path_str).parts[3:])
            if fallback.exists():
                return fallback
    return p


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# Paths to the Google OAuth credentials (already authenticated). Defaults to the
# in-repo google-mcp/ dir — same pattern as tools/google_auth.py. Override via
# GOOGLE_CREDENTIALS_PATH / GOOGLE_TOKEN_PATH in .env.
_DEFAULT_CREDENTIALS = str(
    Path(__file__).resolve().parent.parent.parent / "google-mcp" / "credentials.json"
)
_DEFAULT_TOKEN = str(
    Path(__file__).resolve().parent.parent.parent / "google-mcp" / "token.json"
)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

LIST_EMAILS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_emails",
        "description": (
            "List recent emails from Gmail inbox. Supports Gmail search syntax "
            "(e.g. 'is:unread', 'from:boss@example.com', 'subject:invoice'). "
            "IMPORTANT: for schedule/due-date/upcoming/'do I have X' questions, call "
            "with an EMPTY query and read the returned snippets yourself — reminder "
            "and confirmation emails arrive BEFORE their event date, so after:/before: "
            "date filters and guessed subject keywords miss them. If the empty-query "
            "scan doesn't surface an obvious match, that is NOT a reason to call this "
            "again with a search operator (is:unread, from:, subject:, or any other) — "
            "re-read the snippets or read_email one of them instead. Only use search "
            "operators from the start when the user names a specific sender, subject, or label."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query. Empty string returns recent inbox.",
                    "default": "",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of emails to return (1-20). Default 10.",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
}

READ_EMAIL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_email",
        "description": (
            "Read the full body of a specific email by its message ID. "
            "If the body includes a 'Links found in email:' section (e.g. a YouTube/article "
            "link), open or search using that specific URL/title before doing a generic web "
            "search — the email already points at the exact source."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "Gmail message ID (from list_emails results)",
                },
            },
            "required": ["message_id"],
        },
    },
}

GET_LATEST_EMAIL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_latest_email",
        "description": (
            "Fetch the full content of the most recent email in the inbox (or matching a query). "
            "Returns sender, subject, date, and full body in one call. "
            "Use this instead of list_emails + read_email when you only need the latest email."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional Gmail search query to filter emails (e.g. 'is:unread', 'from:boss@example.com'). Leave empty for latest inbox email.",
                    "default": "",
                },
            },
            "required": [],
        },
    },
}

SEND_EMAIL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_email",
        "description": "Send an email from the authenticated Gmail account, optionally with file attachments.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line",
                },
                "body": {
                    "type": "string",
                    "description": "Plain text email body",
                },
                "attachment_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Absolute local file path(s) to attach to the email",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
}

DOWNLOAD_GMAIL_ATTACHMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "download_gmail_attachment",
        "description": "Download a Gmail attachment to disk. Returns saved file path.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "Gmail message ID containing the attachment",
                },
                "attachment_id": {
                    "type": "string",
                    "description": "Attachment ID (from list_gmail_attachments result)",
                },
                "filename": {
                    "type": "string",
                    "description": "Filename to save as (e.g. 'invoice.pdf')",
                },
                "dest_dir": {
                    "type": "string",
                    "description": "Directory to save into. Default: ~/Downloads",
                    "default": "",
                },
            },
            "required": ["message_id", "attachment_id", "filename"],
        },
    },
}

LIST_GMAIL_ATTACHMENTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_gmail_attachments",
        "description": "Search Gmail inbox for emails with any type of attachment. Use this when user asks about invoices, receipts, or any file attached to an email.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional Gmail search query (e.g. 'from:paypal', 'subject:invoice'). Default: inbox emails with attachments.",
                    "default": "",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max emails to return (1-50). Default: 20.",
                    "default": 20,
                },
            },
            "required": [],
        },
    },
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_gmail_svc = None
_gmail_creds = None  # kept alongside the service for expiry checks


def _gmail_service():
    """
    Return authenticated Gmail API service using the shared Google auth helper.
    """
    return get_service("gmail", "v1")


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

def list_emails(query: str = "", max_results: int = 10) -> str:
    svc = _gmail_service()
    if svc is None:
        return "[gmail error] Not authenticated. Run: python tools/gmail.py --auth"
    try:
        result = execute_with_google_auth_retry(
            lambda: _gmail_service()
            .users()
            .messages()
            .list(
                userId="me",
                q=query or "in:inbox",
                maxResults=min(max_results, 20),
            )
            .execute()
        )

        messages = result.get("messages", [])
        no_match_note = None
        if not messages:
            if query:
                fb = execute_with_google_auth_retry(
                    lambda: _gmail_service()
                    .users()
                    .messages()
                    .list(userId="me", q="in:inbox", maxResults=min(max_results, 20))
                    .execute()
                )
                fb_messages = fb.get("messages", [])
                if fb_messages:
                    messages = fb_messages
                    no_match_note = (
                        f"No emails matched query {query!r} — showing the most recent "
                        f"inbox emails instead; one of these may be what you're looking for."
                    )
            if not messages:
                return "No emails found."

        # One batch HTTP call for all message metadata instead of N sequential
        # round trips — a 20-message list was taking ~50s fetched one at a time.
        fetched: dict[str, dict] = {}

        def _run_batch():
            svc = _gmail_service()
            batch = svc.new_batch_http_request(
                callback=lambda request_id, response, exception: (
                    fetched.__setitem__(request_id, response) if exception is None else None
                )
            )
            for m in messages:
                batch.add(
                    svc.users().messages().get(
                        userId="me", id=m["id"], format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    ),
                    request_id=m["id"],
                )
            batch.execute()

        execute_with_google_auth_retry(_run_batch)

        lines = []
        if no_match_note:
            lines.append(no_match_note)
        for m in messages:
            msg = fetched.get(m["id"])
            if msg is None:
                continue
            headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
            snippet = msg.get("snippet", "")[:80]
            lines.append(
                f"ID: {m['id']}\n"
                f"  From: {headers.get('from', '?')}\n"
                f"  Subject: {headers.get('subject', '(no subject)')}\n"
                f"  Date: {headers.get('date', '?')}\n"
                f"  Preview: {snippet}"
            )
        return "\n\n".join(lines)
    except Exception as e:
        return f"[gmail error] list_emails failed: {e}"


def read_email(message_id: str) -> str:
    svc = _gmail_service()
    if svc is None:
        return "[gmail error] Not authenticated. Run: python tools/gmail.py --auth"
    try:
        msg = execute_with_google_auth_retry(
            lambda: _gmail_service()
            .users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

        headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
        body = _extract_body(msg["payload"])

        out = (
            f"From: {headers.get('from', '?')}\n"
            f"To: {headers.get('to', '?')}\n"
            f"Subject: {headers.get('subject', '(no subject)')}\n"
            f"Date: {headers.get('date', '?')}\n\n"
            f"{body[:4000]}"
        )

        atts = _find_attachments(msg["payload"])
        if atts:
            lines = []
            for a in atts:
                lines.append(
                    f"  {a['filename']} ({a['size']} bytes, {a['kind']}, "
                    f"attachment_id={a['attachment_id']})"
                )
            out += (
                "\n\nAttachments / inline images found:\n"
                + "\n".join(lines)
            )
            extracted = _extract_attachment_contents(message_id, svc)
            if extracted:
                out += "\n\n--- Attachment / inline image contents ---\n" + extracted
        return out
    except Exception as e:
        return f"[gmail error] read_email failed: {e}"

# Standardized attachment-content extraction. Used by read_email / get_latest_email
# so a single call returns body text AND decoded attachment content (OCR for images,
# text for PDFs/docs) — the model never has to chain download → read itself.
def _extract_attachment_contents(message_id: str, svc) -> str:
    """Download every attachment/inline image and decode its text content.

    Returns a formatted block, or "" if there are no decodable attachments.
    Best-effort: a failed extraction logs and moves on — never raises.
    """
    try:
        msg = execute_with_google_auth_retry(
            lambda: svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        )
        atts = _find_attachments(msg["payload"])
    except Exception as e:
        log.warning("attachment probe failed for %s: %s", message_id, e)
        return ""
    if not atts:
        return ""

    import tempfile
    from tools.structured import read_document

    blocks = []
    with tempfile.TemporaryDirectory(prefix="gmail_att_") as td:
        for a in atts:
            fn = a["filename"]
            try:
                path = download_attachment(
                    message_id, a["attachment_id"], fn, td,
                )
            except Exception as e:
                blocks.append(f"  [{fn}: download failed: {e}]")
                continue
            if not path:
                blocks.append(f"  [{fn}: download failed]")
                continue
            try:
                content = read_document(path, max_chars=4000)
            except Exception as e:
                content = f"[read failed: {e}]"
            blocks.append(f"[Attachment {a['kind']}: {fn}]\n{content}")
    return "\n\n".join(blocks)


def get_latest_email(query: str = "") -> str:
    """Fetch the latest email's full content in one call (list + read combined)."""
    svc = _gmail_service()
    if svc is None:
        return "[gmail error] Not authenticated. Run: python tools/gmail.py --auth"
    try:
        result = execute_with_google_auth_retry(
            lambda: _gmail_service()
            .users()
            .messages()
            .list(userId="me", q=query or "in:inbox", maxResults=1)
            .execute()
        )
        messages = result.get("messages", [])
        if not messages:
            return "No emails found."
        return read_email(messages[0]["id"])
    except Exception as e:
        return f"[gmail error] get_latest_email failed: {e}"


# Inline images (USPS Informed Delivery, newsletters) live as image/* parts with
# disp=inline + cid — they ARE the content the user wants read, so include them.
_SUPPORTED_EXTENSIONS = (".pdf", ".xlsx", ".xls", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".gif", ".webp")


def _find_attachments(payload: dict) -> list:
    """
    Recursively walk Gmail message payload parts tree and collect supported attachments
    AND inline images. Supported: PDF, Excel (.xlsx/.xls), Word (.docx/.doc), images.
    Inline images (disp=inline, content-id) are the rendered content of many emails
    (Informed Delivery, flyers) — without them the model sees a stub.
    """
    results = []
    filename = payload.get("filename", "")
    body = payload.get("body", {})
    if filename and filename.lower().endswith(_SUPPORTED_EXTENSIONS) and body.get("attachmentId"):
        is_inline = any(
            h.get("name", "").lower() == "content-disposition" and "inline" in h.get("value", "")
            for h in payload.get("headers", [])
        )
        results.append({
            "filename": filename,
            "attachment_id": body["attachmentId"],
            "size": body.get("size", 0),
            "kind": "inline_image" if is_inline else "attachment",
        })
    for part in payload.get("parts", []):
        results.extend(_find_attachments(part))
    return results


def list_emails_with_attachments(senders: list, seen_ids: set, max_results: int = 20) -> list:
    """
    Search Gmail for emails from any sender in `senders` that have supported attachments
    (PDF, Excel, Word), excluding message IDs in `seen_ids`.
    Returns list of dicts with message_id, sender, subject, date, attachments.
    """
    svc = _gmail_service()
    if svc is None:
        return []
    if not senders:
        return []
    try:
        sender_clause = "(" + " OR ".join("from:" + s for s in senders) + ")"
        # Gmail OR across filename extensions
        ext_clause = "(filename:pdf OR filename:xlsx OR filename:xls OR filename:docx OR filename:doc)"
        query = f"{sender_clause} has:attachment {ext_clause}"
        result = execute_with_google_auth_retry(
            lambda: _gmail_service()
            .users()
            .messages()
            .list(userId="me", q=query, maxResults=min(max_results, 50))
            .execute()
        )
        messages = result.get("messages", [])
        out = []
        for m in messages:
            if m["id"] in seen_ids:
                continue
            msg = execute_with_google_auth_retry(
                lambda message_id=m["id"]: _gmail_service()
                .users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            payload = msg.get("payload", {})
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
            attachments = _find_attachments(payload)
            if not attachments:
                continue
            out.append({
                "message_id": m["id"],
                "sender": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "attachments": attachments,
            })
        return out
    except Exception as e:
        print(f"[gmail] list_emails_with_attachments error: {e}")
        return []


def _find_any_attachments(payload: dict) -> list:
    """Like _find_attachments but any file type, not just PDF/Excel/Word."""
    results = []
    filename = payload.get("filename", "")
    body = payload.get("body", {})
    if filename and body.get("attachmentId"):
        results.append({
            "filename": filename,
            "attachment_id": body["attachmentId"],
            "size": body.get("size", 0),
        })
    for part in payload.get("parts", []):
        results.extend(_find_any_attachments(part))
    return results


def list_all_attachments(seen_ids: set, max_results: int = 50) -> list:
    """
    Search Gmail inbox for any email with any attachment (no sender/type filter),
    excluding message IDs in `seen_ids`.
    Returns list of dicts with message_id, sender, subject, date, internal_date, attachments.
    """
    svc = _gmail_service()
    if svc is None:
        return []
    try:
        query = "in:inbox has:attachment"
        result = execute_with_google_auth_retry(
            lambda: _gmail_service()
            .users()
            .messages()
            .list(userId="me", q=query, maxResults=min(max_results, 100))
            .execute()
        )
        messages = result.get("messages", [])
        out = []
        for m in messages:
            if m["id"] in seen_ids:
                continue
            msg = execute_with_google_auth_retry(
                lambda message_id=m["id"]: _gmail_service()
                .users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            payload = msg.get("payload", {})
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
            attachments = _find_any_attachments(payload)
            if not attachments:
                continue
            out.append({
                "message_id": m["id"],
                "sender": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "internal_date": msg.get("internalDate", ""),
                "attachments": attachments,
            })
        return out
    except Exception as e:
        print(f"[gmail] list_all_attachments error: {e}")
        return []


def download_attachment(message_id: str, attachment_id: str, filename: str, dest_dir: str) -> str:
    """
    Download a Gmail attachment and save it to dest_dir/filename.
    Returns the absolute path string, or "" on error.
    """
    svc = _gmail_service()
    if svc is None:
        return ""
    try:
        att = execute_with_google_auth_retry(
            lambda: _gmail_service()
            .users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        data = base64.urlsafe_b64decode(att["data"] + "==")
        # 2026-07-11: no .expanduser() meant a caller-supplied "~/Desktop/x" created
        # a literal "./~/Desktop/x" relative to CWD instead of the real home dir —
        # confirmed live via email.attachment_watch's config-driven dest_dir.
        dest_dir_path = Path(dest_dir).expanduser()
        dest_dir_path.mkdir(parents=True, exist_ok=True)
        # filename is attacker-controlled (arbitrary email attachment metadata) —
        # strip to a bare basename so "../../.ssh/authorized_keys" or an absolute
        # path can't escape dest_dir (Path.__truediv__ silently drops the left
        # side entirely when the right side is absolute).
        safe_name = Path(filename).name or "attachment"
        dest = dest_dir_path / safe_name
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            dest = dest_dir_path / f"{stem} ({n}){suffix}"
            n += 1
        dest.write_bytes(data)
        return str(dest.resolve())
    except Exception:
        return ""


def send_email(to: str, subject: str, body: str, html_body: str = "", attachment_paths: list[str] | None = None) -> str:
    svc = _gmail_service()
    if svc is None:
        return "[gmail error] Not authenticated. Run: python tools/gmail.py --auth"
    try:
        import mimetypes
        from email.mime.base import MIMEBase
        from email import encoders
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        if html_body:
            body_part = MIMEMultipart("alternative")
            body_part.attach(MIMEText(body, "plain", "utf-8"))
            body_part.attach(MIMEText(html_body, "html", "utf-8"))
        else:
            body_part = MIMEText(body, "plain", "utf-8")

        if attachment_paths:
            msg = MIMEMultipart("mixed")
            msg.attach(body_part)
            for raw_path in attachment_paths:
                path = _resolve_attachment_path(raw_path)
                if not path.is_file():
                    return f"[gmail error] attachment not found: {raw_path}"
                ctype, _ = mimetypes.guess_type(str(path))
                maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
                part = MIMEBase(maintype, subtype)
                part.set_payload(path.read_bytes())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment", filename=path.name)
                msg.attach(part)
        else:
            msg = body_part

        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        execute_with_google_auth_retry(
            lambda: _gmail_service()
            .users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        suffix = f" with {len(attachment_paths)} attachment(s)" if attachment_paths else ""
        return f"Email sent to {to} — subject: '{subject}'{suffix}"
    except Exception as e:
        if "insufficientPermissions" in str(e) or "403" in str(e):
            return (
                "[gmail error] Missing gmail.send scope. "
                "Re-run auth: python tools/gmail.py --auth"
            )
        return f"[gmail error] send_email failed: {e}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_body(payload: dict) -> str:
    """Recursively extract the best text body from a Gmail message payload.

    Prefers text/plain; falls back to text/html (stripped of tags) for
    html-only emails. Snippet is the last resort. Inline images are NOT
    decoded here — they're surfaced via _find_attachments so the caller can
    download + vision-read them."""
    import re as _re
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            links = _re.findall(r'href=["\']([^"\'#][^"\']*)["\']', html, _re.IGNORECASE)
            text = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", html)).strip()
            links = [u for u in dict.fromkeys(links) if u.startswith(("http://", "https://"))]
            if links:
                text += "\n\nLinks found in email:\n" + "\n".join(links[:30])
            return text
    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result
    return payload.get("snippet", "(no body)")


# ---------------------------------------------------------------------------
# Raw data helper for briefing jobs
# ---------------------------------------------------------------------------

def get_recent_emails_raw_status(
    senders: list[str],
    hours: int = 24,
    max_results: int = 10,
) -> tuple[list[dict], str]:
    """
    Return unread emails from any sender in `senders` received in the last
    `hours` hours. Each dict has: message_id, sender, subject, date, snippet.
    """
    svc = _gmail_service()
    if svc is None:
        return [], "Not authenticated. Run: python tools/gmail.py --auth"
    try:
        sender_clause = " OR ".join(f"from:{s}" for s in senders)
        query = f"({sender_clause}) is:unread newer_than:{hours}h"
        result = execute_with_google_auth_retry(
            lambda: _gmail_service()
            .users()
            .messages()
            .list(userId="me", q=query, maxResults=min(max_results, 20))
            .execute()
        )
        messages = result.get("messages", [])
        out = []
        for m in messages:
            msg = execute_with_google_auth_retry(
                lambda message_id=m["id"]: _gmail_service()
                .users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
            out.append({
                "message_id": m["id"],
                "sender": headers.get("From", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", "")[:120],
            })
        return out, ""
    except Exception as e:
        print(f"[gmail] get_recent_emails_raw error: {e}")
        return [], str(e)


def get_recent_emails_raw(
    senders: list[str],
    hours: int = 24,
    max_results: int = 10,
) -> list[dict]:
    """
    Return unread emails from any sender in `senders` received in the last
    `hours` hours. Returns [] if not authenticated or on API error.
    """
    emails, _ = get_recent_emails_raw_status(senders, hours=hours, max_results=max_results)
    return emails


# ---------------------------------------------------------------------------
# One-time auth CLI
# ---------------------------------------------------------------------------

def _run_auth():
    """
    Re-run OAuth with full scopes (read + send).
    Uses existing credentials.json from gmail-mcp — no new Google Cloud setup needed.
    Saves refreshed token back to the same token.json path.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow
    import json

    creds_path = Path(os.environ.get("GOOGLE_CREDENTIALS_PATH", _DEFAULT_CREDENTIALS))
    token_path = Path(os.environ.get("GOOGLE_TOKEN_PATH", _DEFAULT_TOKEN))

    if not creds_path.exists():
        print(f"credentials.json not found at {creds_path}")
        return

    # Wrap web credentials as installed-app format for InstalledAppFlow
    raw = json.loads(creds_path.read_text())
    web = raw.get("web", {})
    client_config = {
        "installed": {
            "client_id": web["client_id"],
            "client_secret": web["client_secret"],
            "redirect_uris": ["http://localhost"],
            "auth_uri": web.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
            "token_uri": web.get("token_uri", "https://oauth2.googleapis.com/token"),
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=3333)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    print(f"Token saved to {token_path} (scopes: {SCOPES})")


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    if "--auth" in sys.argv:
        _run_auth()
    else:
        print("Usage: python tools/gmail.py --auth")
