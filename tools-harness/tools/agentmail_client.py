"""Python wrapper for Autopilot Mail REST API.

Per-agent email inboxes via `@autopilot-mail/server` on :3100.
Outbound delivery via Resend SMTP (bch-agent@resend.dev).

See docs/agents/agent-mail.md for full architecture and review.

Usage:
    import agentmail_client as am
    inbox = am.create_inbox("my-agent")
    am.send_message(inbox["inbox_id"], "user@example.com",
                    subject="Hello", text="Body")
"""

import json
import logging
import os
import urllib.request
import urllib.error

_log = logging.getLogger(__name__)

BASE_URL = os.environ.get("AGENTMAIL_BASE_URL", "http://localhost:3100/v0")
API_KEY = os.environ.get("AGENTMAIL_API_KEY", "clixen-test-key")


def _req(method: str, path: str, body: dict | None = None) -> list | dict | str:
    url = f"{BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    hdrs = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data=data, headers=hdrs, method=method), timeout=10)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            d = json.loads(detail)
            detail = d.get("error", detail)
        except Exception:
            pass
        return f"[agentmail error] HTTP {e.code}: {detail}"
    except urllib.error.URLError as e:
        return f"[agentmail error] {e.reason}"
    except Exception as e:
        return f"[agentmail error] {e}"


def create_inbox(username: str) -> dict | str:
    result = _req("POST", "/inboxes", {"username": username})
    if isinstance(result, str):
        return result
    return {
        "inbox_id": result["inbox_id"],
        "email": result["email"],
        "created_at": result["created_at"],
    }


def list_inboxes() -> list[dict] | str:
    result = _req("GET", "/inboxes")
    if isinstance(result, str):
        return result
    return result.get("inboxes", [])


def get_inbox(inbox_id: str) -> dict | str:
    return _req("GET", f"/inboxes/{inbox_id}")


def delete_inbox(inbox_id: str) -> bool | str:
    result = _req("DELETE", f"/inboxes/{inbox_id}")
    if isinstance(result, str):
        return result
    return True


def send_message(inbox_id: str, to: str | list[str], subject: str = "", text: str = "", html: str | None = None) -> dict | str:
    body = {"to": [to] if isinstance(to, str) else to, "subject": subject, "text": text}
    if html:
        body["html"] = html
    result = _req("POST", f"/inboxes/{inbox_id}/messages/send", body)
    if isinstance(result, str):
        return result
    return {
        "message_id": result["message_id"],
        "thread_id": result["thread_id"],
        "timestamp": result["timestamp"],
    }


def reply_to_message(inbox_id: str, message_id: str, text: str = "", html: str | None = None) -> dict | str:
    body = {"text": text}
    if html:
        body["html"] = html
    result = _req("POST", f"/inboxes/{inbox_id}/messages/{message_id}/reply", body)
    if isinstance(result, str):
        return result
    return {
        "message_id": result["message_id"],
        "thread_id": result["thread_id"],
        "timestamp": result["timestamp"],
    }


def list_threads(inbox_id: str) -> list[dict] | str:
    result = _req("GET", f"/inboxes/{inbox_id}/threads")
    if isinstance(result, str):
        return result
    return result.get("threads", [])


def get_thread(inbox_id: str, thread_id: str) -> dict | str:
    return _req("GET", f"/inboxes/{inbox_id}/threads/{thread_id}")


def get_message(inbox_id: str, message_id: str) -> dict | str:
    return _req("GET", f"/inboxes/{inbox_id}/messages/{message_id}")


def health() -> str:
    result = _req("GET", "/inboxes")
    if isinstance(result, str):
        return f"unhealthy: {result}"
    return "healthy"

