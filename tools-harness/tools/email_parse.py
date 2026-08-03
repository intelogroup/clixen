"""Parse email files (.eml, .msg, .mbox) using `unstructured`.

`docling` doesn't handle email containers; `unstructured` does. We expose a
focused `parse_email_file` so the agent can answer "summarize this .eml
attachment" or "extract the addresses from this Outlook .msg".

For raw .eml we also fall back to Python's stdlib `email` module if
unstructured isn't installed — keeps the tool functional with zero deps.
"""
from __future__ import annotations

from pathlib import Path

_DEFAULT_MAX_CHARS = 8000
_SUPPORTED_EXTS = {".eml", ".msg", ".mbox"}


SCHEMA = {
    "type": "function",
    "function": {
        "name": "parse_email_file",
        "description": (
            "Extract subject, from/to/cc, date, and body text from an .eml/.msg/.mbox file. "
            "Use when the user has a saved email file (e.g. dragged out of Mail.app, "
            "downloaded from Gmail) and wants to summarize or extract details. "
            "Returns structured headers + plain-text body."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the .eml/.msg/.mbox file. ~ is expanded.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": f"Truncate body to this many chars (default {_DEFAULT_MAX_CHARS}).",
                    "default": _DEFAULT_MAX_CHARS,
                },
            },
            "required": ["path"],
        },
    },
}


def _stdlib_eml_parse(path: Path) -> dict:
    """Fallback parser using Python's email stdlib for plain .eml files."""
    from email import policy
    from email.parser import BytesParser
    with path.open("rb") as fh:
        msg = BytesParser(policy=policy.default).parse(fh)
    headers = {
        "subject": msg.get("subject", ""),
        "from": msg.get("from", ""),
        "to": msg.get("to", ""),
        "cc": msg.get("cc", ""),
        "date": msg.get("date", ""),
    }
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body = part.get_content()
                    break
                except Exception:
                    continue
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    try:
                        body = part.get_content()
                        break
                    except Exception:
                        continue
    else:
        try:
            body = msg.get_content()
        except Exception:
            body = ""
    return {"headers": headers, "body": body}


def execute(path: str, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    p = Path(path or "").expanduser()
    if not p.exists():
        return f"[parse_email_file error] file not found: {p}"
    if not p.is_file():
        return f"[parse_email_file error] not a file: {p}"
    if p.suffix.lower() not in _SUPPORTED_EXTS:
        return (
            f"[parse_email_file error] unsupported extension {p.suffix!r}. "
            f"Supported: {sorted(_SUPPORTED_EXTS)}"
        )

    headers: dict = {}
    body = ""

    # Prefer unstructured (handles .msg + .mbox + edge-case .eml encodings).
    try:
        if p.suffix.lower() == ".eml":
            from unstructured.partition.email import partition_email
            elements = partition_email(filename=str(p))
        elif p.suffix.lower() == ".msg":
            from unstructured.partition.msg import partition_msg
            elements = partition_msg(filename=str(p))
        else:  # .mbox
            from unstructured.partition.email import partition_email
            elements = partition_email(filename=str(p))

        body_parts = []
        for el in elements:
            md = getattr(el, "metadata", None)
            if md is not None:
                for fld in ("subject", "sent_from", "sent_to", "cc_recipient", "date"):
                    val = getattr(md, fld, None)
                    if val and fld not in headers:
                        headers[fld] = val if isinstance(val, str) else ", ".join(map(str, val))
            txt = getattr(el, "text", "") or ""
            if txt.strip():
                body_parts.append(txt)
        body = "\n\n".join(body_parts)
    except ImportError:
        if p.suffix.lower() == ".eml":
            try:
                parsed = _stdlib_eml_parse(p)
                headers = parsed["headers"]
                body = parsed["body"]
            except Exception as e:
                return f"[parse_email_file error] stdlib fallback failed: {e}"
        else:
            return (
                f"[parse_email_file error] `unstructured` not installed and "
                f"stdlib fallback only handles .eml. Run: pip install 'unstructured[email]'"
            )
    except Exception as e:
        return f"[parse_email_file error] parse failed: {e}"

    cap = max(500, min(int(max_chars or _DEFAULT_MAX_CHARS), 30000))
    if len(body) > cap:
        body = body[:cap] + f"\n... [truncated; {len(body) - cap} more chars]"

    out = []
    if headers:
        out.append("# Headers")
        for k, v in headers.items():
            if v:
                out.append(f"  {k}: {v}")
        out.append("")
    out.append("# Body")
    out.append(body or "(no body extracted)")
    return "\n".join(out)
