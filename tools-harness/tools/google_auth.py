"""
Shared Google OAuth2 helper for all Google API tools.

One token file covers all scopes (Gmail, Calendar, Tasks).
To re-auth with expanded scopes: python tools/google_auth.py --auth
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

# get_service() caches one googleapiclient service per api:version, backed by
# httplib2.Http — which googleapiclient documents as not thread-safe. Multiple
# tool calls (e.g. several read_email calls in one LLM round) dispatch concurrently
# via cloud_client.py's ThreadPoolExecutor, and were racing on that shared
# transport, throwing intermittent exceptions that got misreported as tool
# failures and triggered spurious model escalation. Serialize here — the one
# chokepoint every Google API call already routes through.
_google_api_lock = threading.Lock()

# All scopes across all tools — re-auth once to get all
ALL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
    "https://www.googleapis.com/auth/postmaster.readonly",
]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CREDENTIALS = str(_REPO_ROOT / "google-mcp" / "credentials.json")
_DEFAULT_TOKEN = str(_REPO_ROOT / "google-mcp" / "token.json")

_services: dict[str, object] = {}


def _http_status(exc: Exception) -> int | None:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def is_google_scope_error(exc: Exception) -> bool:
    """True when OAuth scopes are insufficient and browser re-consent is required."""
    text = str(exc).lower()
    return (
        _http_status(exc) == 403
        and ("insufficient" in text)
        and ("scope" in text or "permission" in text)
    )


def is_refreshable_google_auth_error(exc: Exception) -> bool:
    """True for auth failures that can usually be fixed with a refresh token."""
    if is_google_scope_error(exc):
        return False
    status = _http_status(exc)
    if status == 401:
        return True
    text = str(exc).lower()
    return "invalid credentials" in text or "access token expired" in text


def execute_with_google_auth_retry(call_factory):
    """
    Execute a Google API call and refresh/retry once on recoverable auth errors.

    call_factory must create/fetch the service inside the callable, not close over
    an old service object. That lets the retry use the refreshed credentials.
    """
    with _google_api_lock:
        try:
            return call_factory()
        except Exception as exc:
            if not is_refreshable_google_auth_error(exc):
                raise
            result = refresh_google_token()
            if not result.startswith("Token refreshed successfully"):
                raise
            return call_factory()

# ---------------------------------------------------------------------------
# Programmatic token refresh — no browser required
# ---------------------------------------------------------------------------

REFRESH_TOKEN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "refresh_google_token",
        "description": (
            "Refresh the Google OAuth2 access token using the stored refresh token. "
            "Call this when a Google tool returns a 401, 403, or authentication error. "
            "No browser interaction required — works fully programmatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def refresh_google_token() -> str:
    """
    Exchange the stored refresh_token for a new access_token via the Google
    token endpoint. Saves updated credentials and clears the service cache
    so the next tool call picks up the fresh token.
    Returns a status string.
    """
    import urllib.parse
    import urllib.request

    token_path = Path(os.environ.get("GOOGLE_TOKEN_PATH", _DEFAULT_TOKEN))
    creds_path = Path(os.environ.get("GOOGLE_CREDENTIALS_PATH", _DEFAULT_CREDENTIALS))

    if not token_path.exists():
        return "[google_auth] No token file found. Run: python tools/google_auth.py --auth"

    raw = json.loads(token_path.read_text())
    creds_raw = json.loads(creds_path.read_text()) if creds_path.exists() else {}

    # Extract refresh_token and client credentials
    refresh_token = raw.get("refresh_token")
    if not refresh_token:
        return "[google_auth] No refresh_token in token file. Re-auth required: python tools/google_auth.py --auth"

    client_info = creds_raw.get("web", creds_raw.get("installed", {}))
    client_id = raw.get("client_id") or client_info.get("client_id", "")
    client_secret = raw.get("client_secret") or client_info.get("client_secret", "")

    if not client_id or not client_secret:
        return "[google_auth] Missing client_id or client_secret."

    try:
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }).encode()

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            token_resp = json.loads(resp.read())

        # Update token file — preserve refresh_token (Google doesn't always return a new one)
        from datetime import timezone, timedelta
        expiry = (
            datetime.now(timezone.utc) + timedelta(seconds=token_resp.get("expires_in", 3600))
        ).isoformat()

        scopes = token_resp.get("scope", "").split() or raw.get("scopes", ALL_SCOPES)

        updated = {
            **raw,
            "token": token_resp["access_token"],
            "expiry": expiry,
            "scopes": scopes,
        }
        if "refresh_token" in token_resp:
            updated["refresh_token"] = token_resp["refresh_token"]

        token_path.write_text(json.dumps(updated, indent=2))

        # Clear cached service clients so next call re-authenticates
        _services.clear()

        scopes = token_resp.get("scope", "").split()
        return (
            f"Token refreshed successfully. "
            f"Expires: {expiry}. "
            f"Scopes: {len(scopes)} active."
        )

    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return f"[google_auth] Refresh failed ({e.code}): {body}"
    except Exception as e:
        return f"[google_auth] Refresh error: {e}"


def _maybe_refresh_token():
    """Proactively refresh token if < 5 min from expiry. Prevents 401 on stale cached service."""
    import json
    token_path = Path(os.environ.get("GOOGLE_TOKEN_PATH", _DEFAULT_TOKEN))
    if not token_path.exists():
        return
    try:
        raw = json.loads(token_path.read_text())
        expiry = raw.get("expiry") or raw.get("expiry_date")
        if not expiry:
            return
        if isinstance(expiry, (int, float)):
            exp_dt = datetime.fromtimestamp(expiry / 1000)
        else:
            exp_dt = datetime.fromisoformat(str(expiry).replace("Z", "+00:00").split("+")[0])
        if (exp_dt - datetime.now()).total_seconds() < 300:
            refresh_google_token()
            _services.clear()
    except Exception:
        pass


def get_service(api: str, version: str):
    """
    Return an authenticated Google API service client.
    Caches per api:version — safe to call repeatedly.
    Handles both Python google-auth token format and Node.js googleapis format.
    """
    key = f"{api}:{version}"
    if key in _services:
        _maybe_refresh_token()
        return _services[key]

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token_path = Path(os.environ.get("GOOGLE_TOKEN_PATH", _DEFAULT_TOKEN))
    creds_path = Path(os.environ.get("GOOGLE_CREDENTIALS_PATH", _DEFAULT_CREDENTIALS))

    if not token_path.exists():
        return None

    raw = json.loads(token_path.read_text())
    creds_raw = json.loads(creds_path.read_text()) if creds_path.exists() else {}

    # Node.js googleapis format has 'access_token' instead of 'token'
    if "access_token" in raw and "token" not in raw:
        client_info = creds_raw.get("web", creds_raw.get("installed", {}))
        expiry = None
        if raw.get("expiry_date"):
            expiry = datetime.utcfromtimestamp(raw["expiry_date"] / 1000)
        creds = Credentials(
            token=raw["access_token"],
            refresh_token=raw.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_info.get("client_id", os.environ.get("GOOGLE_CLIENT_ID", "")),
            client_secret=client_info.get("client_secret", os.environ.get("GOOGLE_CLIENT_SECRET", "")),
            scopes=raw.get("scope", " ".join(ALL_SCOPES)).split(),
            expiry=expiry,
        )
    elif "token" in raw:
        # Python google-auth format (written by run_auth())
        client_info = creds_raw.get("web", creds_raw.get("installed", {}))
        expiry = None
        if raw.get("expiry"):
            try:
                expiry = datetime.fromisoformat(raw["expiry"].replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass
        creds = Credentials(
            token=raw["token"],
            refresh_token=raw.get("refresh_token"),
            token_uri=raw.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=raw.get("client_id") or client_info.get("client_id", ""),
            client_secret=raw.get("client_secret") or client_info.get("client_secret", ""),
            scopes=raw.get("scopes", ALL_SCOPES),
            expiry=expiry,
        )
    else:
        creds = Credentials.from_authorized_user_file(str(token_path), ALL_SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())

    svc = build(api, version, credentials=creds)
    _services[key] = svc
    return svc


def run_auth():
    """
    Manual OAuth2 flow for web-type credentials.
    Starts a minimal HTTP server on port 3333 to catch the redirect,
    exchanges the code for tokens with all scopes.
    """
    import urllib.parse
    import urllib.request
    import webbrowser
    from http.server import HTTPServer, BaseHTTPRequestHandler

    creds_path = Path(os.environ.get("GOOGLE_CREDENTIALS_PATH", _DEFAULT_CREDENTIALS))
    token_path = Path(os.environ.get("GOOGLE_TOKEN_PATH", _DEFAULT_TOKEN))

    if not creds_path.exists():
        print(f"credentials.json not found at {creds_path}")
        return

    raw = json.loads(creds_path.read_text())
    web = raw.get("web", {})
    client_id = web["client_id"]
    client_secret = web["client_secret"]
    redirect_uri = "http://localhost:3333"

    # Build authorization URL
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(ALL_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)

    # Catch the auth code via a one-shot HTTP server
    auth_code = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            code = qs.get("code", [None])[0]
            if code:
                auth_code.append(code)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h2>Authorized! You can close this tab.</h2>")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h2>Missing code. Try again.</h2>")

        def log_message(self, *args):
            pass  # silence request logs

    server = HTTPServer(("localhost", 3333), Handler)
    print(f"\nOpening browser for Google consent...\n{auth_url}\n")
    webbrowser.open(auth_url)
    print("Waiting for redirect on http://localhost:3333 ...")
    server.handle_request()  # blocks until one request comes in
    server.server_close()

    if not auth_code:
        print("No auth code received.")
        return

    # Exchange code for tokens
    token_data = urllib.parse.urlencode({
        "code": auth_code[0],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        token_resp = json.loads(resp.read())

    # Save in Python google-auth format
    from datetime import datetime, timezone, timedelta
    expiry = datetime.now(timezone.utc) + timedelta(seconds=token_resp.get("expires_in", 3600))
    token_json = {
        "token": token_resp["access_token"],
        "refresh_token": token_resp.get("refresh_token"),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": token_resp.get("scope", " ".join(ALL_SCOPES)).split(),
        "expiry": expiry.isoformat(),
    }

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(token_json, indent=2))
    print(f"\nToken saved: {token_path}")
    print(f"Scopes: {token_json['scopes']}")


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    if "--auth" in sys.argv:
        run_auth()
    else:
        print("Usage: python tools/google_auth.py --auth")
