"""
Clixen chat UI — http://localhost:9234
Run:  python chat_ui.py
"""

import os
import sys

# Ensure the project root (parent of tools-harness/) is on sys.path so that
# `src.g4l` is importable regardless of which directory chat_ui.py is run from.
_PROJECT_ROOT = str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import hmac
import io
import json
import mimetypes
import re
import secrets
import subprocess
import queue as queue_mod
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from harness import run as harness_run
from automation_catalog import list_automations, build_dispatch_payload, get_automation
from clients.ollama_client import warmup as ollama_warmup, DEFAULT_MODEL as _OLLAMA_DEFAULT_MODEL
from clients.router import TASK_ROUTING
from clients.cloud_client import DEFAULT_CLOUD_MODEL, _TOOL_PROGRESS_LABELS
from tools.websearch import search as _websearch

from tools.query_guard import check_all as _guard_check
from store.conversation import get as conv_get, clear as conv_clear
from clients.router import warm_classifier
from store import upload_store, automation_store, workflow_store
from jobs import job_queue
from skills_hub import (
    list_skills as _list_skills,
    list_categories as _list_categories,
    match_skill as _match_skill,
    dispatch_with_prompt as _dispatch_skill,
)

# Import local Pydantic models from this file (relocated from src.g4l.core)
from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    chat_id: str = "web_ui"


class TTSSpeakRequest(BaseModel):
    text: str
    voice: str = "af_heart"


class MockLoginRequest(BaseModel):
    display_name: str
    email: str
    workspace_name: str = "Local Workspace"
    role: str = "Owner"


class LocalRegisterRequest(BaseModel):
    display_name: str
    email: str
    password: str
    workspace_name: str = "Local Workspace"
    role: str = "Owner"


class LocalLoginRequest(BaseModel):
    email: str
    password: str


class LocalResetPasswordRequest(BaseModel):
    email: str
    password: str


class WorkflowCreateRequest(BaseModel):
    automation_id: str
    task_name: str = ""
    config: dict = {}
    schedule: dict = {}
    status: str = "active"
    next_run_at: Optional[str] = None
    dedupe_key: str = ""


class ApprovalCreateRequest(BaseModel):
    kind: str
    payload: dict = {}
    workflow_instance_id: str = ""
    job_id: str = ""
    expires_at: Optional[str] = None


class FSWrite(BaseModel):
    path: str
    content: str


class FSRun(BaseModel):
    path: str


class IdeIndexRequest(BaseModel):
    path: str


class TaskDispatchRequest(BaseModel):
    task_name: str
    params: dict = {}



class AutomationRunRequest(BaseModel):
    overrides: dict = {}



class AutomationPresetRequest(BaseModel):
    params: dict = {}



class SkillDispatchRequest(BaseModel):
    skill_id: str
    message: str
    chat_id: str = "skills_ui"
    images: list = []


# sentence_boundary function (relocated from src.g4l.core.utils)
def sentence_boundary(buf: str):
    import re

    if not buf:
        return "", ""
    for marker in (".\n", "!\n", "?\n", "\n\n"):
        idx = buf.find(marker)
        if idx != -1:
            chunk = buf[: idx + len(marker)].strip()
            rest = buf[idx + len(marker) :]
            if len(chunk.split()) >= 3:
                return chunk, rest
    m = re.search(r"([.!?…])\s+(?=[A-Z])", buf)
    if m and len(buf[:m.end()].split()) >= 4:
        return buf[:m.end()].strip(), buf[m.end():]
    return "", buf


def _sentence_boundary(buf: str) -> tuple[str, str]:
    return sentence_boundary(buf)


from log_config import setup_logging

_log = setup_logging("chat_ui", log_file="chat_ui.log")

from clients.cancellation import QueryAbortedException

_active_streams: dict[str, threading.Event] = {}
_active_streams_lock = threading.Lock()

def abort_active_stream(chat_id: str):
    if not chat_id:
        return
    with _active_streams_lock:
        keys_to_cancel = [k for k in _active_streams if k == chat_id or k.startswith(f"{chat_id}_")]
        for k in keys_to_cancel:
            event = _active_streams.get(k)
            if event:
                event.set()

def register_active_stream(chat_id: str, key: str = None) -> threading.Event:
    if key is None:
        key = chat_id
    abort_active_stream(chat_id)
    event = threading.Event()
    with _active_streams_lock:
        _active_streams[key] = event
    return event

_MD_STRIP_RE = re.compile(r"(\*{1,3}|_{1,3}|`{1,3}|~~|#{1,6}\s?)")
_TTS_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# Kokoro reads emoji aloud by codepoint/gibberish instead of skipping them —
# same fix as brabble_hook.py's _EMOJI_RE, ported here since this TTS path
# never had it.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "️"
    "‍"
    "]+"
)
_kokoro_lock = threading.Lock()
_voice_queues: set[asyncio.Queue] = set()
_voice_queues_lock = threading.Lock()

# ollama_client.py/cloud_client.py inject these progress labels into the token
# stream for the web UI's live bubble (e.g. "🔍 Searching the web…"). The chat
# bubble accumulates raw tokens verbatim, so the Speak button posts them to TTS
# too — same bug brabble_hook.py already fixed for voice. Match only the exact
# known labels (matching on emoji alone would eat real content, e.g. "☁️
# Condition:" in a weather answer). \n* not \n\n since a glued/no-separator
# label was seen live.
_LABEL_ALT = "|".join(re.escape(l) for l in _TOOL_PROGRESS_LABELS.values())
_TOOL_PROGRESS_RE = re.compile(rf"\n*(?:{_LABEL_ALT})") if _LABEL_ALT else None


def _strip_md(text: str) -> str:
    clean = text or ""
    if _TOOL_PROGRESS_RE:
        clean = _TOOL_PROGRESS_RE.sub("", clean)
    clean = _MD_STRIP_RE.sub("", clean)
    return _EMOJI_RE.sub("", clean).strip()


def _split_sentences(text: str) -> list[str]:
    clean = _strip_md(text)
    if not clean:
        return []
    parts = [chunk.strip() for chunk in _TTS_SPLIT_RE.split(clean) if chunk.strip()]
    return parts or [clean]


def _default_kokoro_path(filename: str) -> str:
    return str((Path(__file__).resolve().parent.parent / "models" / filename).resolve())


def _kokoro_model_path() -> str:
    return os.environ.get("KOKORO_ONNX_PATH", _default_kokoro_path("kokoro-v1.0.onnx"))


def _kokoro_voices_path() -> str:
    return os.environ.get("KOKORO_VOICES_PATH", _default_kokoro_path("voices-v1.0.bin"))


@lru_cache(maxsize=1)
def _get_kokoro():
    with _kokoro_lock:
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        providers = []
        env_provider = os.environ.get("ONNX_PROVIDER")
        if env_provider:
            providers.append(env_provider)
        if "CoreMLExecutionProvider" in ort.get_available_providers():
            providers.append("CoreMLExecutionProvider")
        providers.append("CPUExecutionProvider")

        seen = set()
        ordered_providers = [p for p in providers if not (p in seen or seen.add(p))]
        last_exc = None
        original_provider = os.environ.get("ONNX_PROVIDER")
        for provider in ordered_providers:
            try:
                os.environ["ONNX_PROVIDER"] = provider
                return Kokoro(_kokoro_model_path(), _kokoro_voices_path())
            except Exception as exc:
                last_exc = exc
                _log.warning("Kokoro init failed with %s: %s", provider, exc)
        if original_provider is None:
            os.environ.pop("ONNX_PROVIDER", None)
        else:
            os.environ["ONNX_PROVIDER"] = original_provider
        raise last_exc


def _wav_bytes(samples, sample_rate: int) -> bytes:
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    return buf.getvalue()


def _synthesize(text: str, voice: str = "af_heart") -> bytes:
    clean = _strip_md(text)
    if not clean:
        return b""
    samples, sample_rate = _get_kokoro().create(clean, voice=voice, speed=1.0, lang="en-us")
    return _wav_bytes(samples, sample_rate)


@lru_cache(maxsize=256)
def _synthesize_cached(text: str, voice: str = "af_heart") -> bytes:
    return _synthesize(text, voice)

# ---------------------------------------------------------------------------
# Local auth + workspace state
# ---------------------------------------------------------------------------

_workspace_lock = threading.Lock()
WORKSPACE_STATE_PATH = Path(
    os.environ.get(
        "G4L_WORKSPACE_STATE_PATH",
        Path(__file__).parent / "workspace_state.json",
    )
)
SESSION_COOKIE = "g4l_session"
SESSION_TTL_SEC = 60 * 60 * 24 * 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_workspace_state() -> dict:
    return {
        "session_secret": secrets.token_hex(32),
        "account": {
            "email": "owner@local.dev",
            "display_name": "Owner",
            "workspace_name": "Local Workspace",
            "role": "Owner",
            "avatar": "OW",
            "password_salt": "",
            "password_hash": "",
            "created_at": _now_iso(),
            "last_login_at": _now_iso(),
        },
        "lifecycle": {
            "first_message_sent": False,
        },
    }


def _persist_workspace_state() -> None:
    WORKSPACE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write (temp + replace): a crash/kill mid-write previously truncated
    # the live file, and the next boot clobbered it with defaults — wiping the
    # configured account and rotating the session secret.
    tmp_path = WORKSPACE_STATE_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(_workspace_state, indent=2), encoding="utf-8")
    tmp_path.replace(WORKSPACE_STATE_PATH)


# 2026-08-03: this exact secret was committed to git history (workspace_state.json
# was tracked before it was gitignored). Treat it as compromised: any session cookie
# signed with it must stop validating immediately.
_LEAKED_SESSION_SECRET = "eb64ceead8e7e6ca0cd9bd1f6630781c29044e34d22162c233f3fd649ea8f720"


def _load_workspace_state() -> dict:
    state = _default_workspace_state()
    if WORKSPACE_STATE_PATH.exists():
        try:
            loaded = json.loads(WORKSPACE_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state.update({k: v for k, v in loaded.items() if k in state})
        except Exception:
            # Preserve the corrupt file for recovery instead of overwriting it
            # with defaults on the next persist (was silently wiping the
            # configured account + rotating the session secret).
            _log.warning("Failed to load workspace state from %s", WORKSPACE_STATE_PATH)
            try:
                import shutil as _sh
                _sh.copy2(WORKSPACE_STATE_PATH, str(WORKSPACE_STATE_PATH) + f".corrupt-{int(time.time())}")
            except Exception:
                pass
    if not state.get("account"):
        state["account"] = _default_workspace_state()["account"]
    if not state.get("session_secret") or state["session_secret"] == _LEAKED_SESSION_SECRET:
        if state.get("session_secret") == _LEAKED_SESSION_SECRET:
            _log.warning("Rotating compromised session_secret from git history")
        state["session_secret"] = secrets.token_hex(32)
    return state


_workspace_state = _load_workspace_state()
_persist_workspace_state()


def _avatar_for(name: str, fallback: str = "G4") -> str:
    initials = "".join(part[:1] for part in name.split()[:2]).upper()
    return initials or fallback


def _hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return salt.hex(), derived.hex()


def _verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    _, actual_hex = _hash_password(password, salt_hex=salt_hex)
    return hmac.compare_digest(actual_hex, expected_hex)


def _sign_session(payload: dict) -> str:
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    secret = _workspace_state["session_secret"].encode("utf-8")
    sig = hmac.new(secret, body.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{body}.{sig_b64}"


def _unsign_session(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        secret = _workspace_state["session_secret"].encode("utf-8")
        expected = base64.urlsafe_b64encode(
            hmac.new(secret, body.encode("utf-8"), hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")
        if not hmac.compare_digest(sig, expected):
            return None
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if payload.get("exp", 0) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return payload
    except Exception:
        return None


def _set_session_cookie(response: Response, email: str) -> None:
    token = _sign_session(
        {
            "email": email,
            "exp": int(datetime.now(timezone.utc).timestamp()) + SESSION_TTL_SEC,
        }
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_SEC,
        secure=False,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax")


def _dev_auto_auth_setup() -> None:
    """In dev mode, auto-sync the account with DEV_EMAIL / DEV_PASSWORD env vars."""
    dev_email = os.environ.get("DEV_EMAIL", "").strip()
    dev_password = os.environ.get("DEV_PASSWORD", "")
    if not dev_email or not dev_password:
        return
    dev_email = dev_email.lower()
    with _workspace_lock:
        account = _workspace_state.get("account")
        if not account:
            salt_hex, password_hash = _hash_password(dev_password)
            _workspace_state["account"] = {
                "email": dev_email,
                "display_name": "Dev User",
                "workspace_name": "Dev Workspace",
                "role": "Owner",
                "avatar": _avatar_for("Dev User"),
                "password_salt": salt_hex,
                "password_hash": password_hash,
                "created_at": _now_iso(),
                "last_login_at": _now_iso(),
            }
            _persist_workspace_state()
        elif not _verify_password(dev_password, account["password_salt"], account["password_hash"]):
            salt_hex, password_hash = _hash_password(dev_password)
            account["password_salt"] = salt_hex
            account["password_hash"] = password_hash
            _persist_workspace_state()


_dev_auto_auth_setup()


def _current_account(request: Request) -> dict | None:
    client_host = request.client.host if request.client else ""
    is_local = client_host in ("127.0.0.1", "::1", "localhost", "testclient", "")
    
    with _workspace_lock:
        account = deepcopy(_workspace_state.get("account"))

    # If loopback request, auto-authenticate as the owner account
    if is_local:
        return account

    # For non-local/external requests, require a valid session cookie
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    payload = _unsign_session(token)
    if not payload:
        return None
    if account and account.get("email", "").lower() != str(payload.get("email", "")).lower():
        return None
    return account


def _require_auth(request: Request) -> dict:
    account = _current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="authentication required")
    return account


def _public_user(account: dict | None, authenticated: bool) -> dict:
    if account and authenticated:
        return {
            "authenticated": True,
            "display_name": account["display_name"],
            "email": account["email"],
            "workspace_name": account["workspace_name"],
            "role": account["role"],
            "plan": "Offline",
            "avatar": account["avatar"],
            "last_login_at": account.get("last_login_at"),
        }
    return {
        "authenticated": False,
        "display_name": "Guest",
        "email": "",
        "workspace_name": account["workspace_name"] if account else "Local Workspace",
        "role": account["role"] if account else "Owner",
        "plan": "Offline",
        "avatar": "G4",
        "last_login_at": None,
    }


def _gmail_connection_snapshot() -> dict:
    from tools import gmail as gmail_tools

    token_path = Path(
        os.path.expanduser(os.environ.get("GOOGLE_TOKEN_PATH", gmail_tools._DEFAULT_TOKEN))
    )
    credentials_path = Path(
        os.path.expanduser(
            os.environ.get("GOOGLE_CREDENTIALS_PATH", gmail_tools._DEFAULT_CREDENTIALS)
        )
    )
    connected = token_path.exists() and credentials_path.exists()
    return {
        "connected": connected,
        "token_path": str(token_path),
        "credentials_path": str(credentials_path),
        "detail": "Existing Gmail token detected"
        if connected
        else "No Gmail token detected locally",
    }


def _whatsapp_connection_snapshot() -> dict:
    import httpx

    bridge_url = os.environ.get("WHATSAPP_BRIDGE_URL", "http://localhost:9235")
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{bridge_url}/status")
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "unknown")
                connected = status == "connected"
                return {
                    "connected": connected,
                    "detail": "WhatsApp connected"
                    if connected
                    else "WhatsApp pending - scan QR code",
                    "status": status,
                }
    except Exception:
        pass
    return {
        "connected": False,
        "detail": "WhatsApp bridge unavailable",
        "status": "unavailable",
    }


def _build_workspace_payload(current_account: dict | None = None) -> dict:
    with _workspace_lock:
        state = deepcopy(_workspace_state)
    account = state.get("account")
    authenticated = current_account is not None
    gmail_live = _gmail_connection_snapshot()
    whatsapp_live = _whatsapp_connection_snapshot()
    lifecycle_steps = [
        {
            "id": "login",
            "label": "Sign in locally",
            "done": authenticated,
            "detail": current_account["email"]
            if authenticated
            else ("Local account locked" if account else "No local account configured"),
        },
        {
            "id": "gmail",
            "label": "Detect Gmail token",
            "done": gmail_live["connected"],
            "detail": gmail_live["detail"],
        },
        {
            "id": "whatsapp",
            "label": "Connect WhatsApp",
            "done": whatsapp_live["connected"],
            "detail": whatsapp_live["detail"],
        },
        {
            "id": "first_message",
            "label": "Complete first chat",
            "done": state["lifecycle"]["first_message_sent"],
            "detail": "First message sent"
            if state["lifecycle"]["first_message_sent"]
            else "No completed chat yet",
        },
    ]
    completion = int(sum(1 for s in lifecycle_steps if s["done"]) / len(lifecycle_steps) * 100)
    return {
        "user": _public_user(account, authenticated),
        "auth": {
            "has_account": bool(account),
            "authenticated": authenticated,
        },
        "integrations": {
            "gmail": gmail_live,
            "whatsapp": whatsapp_live,
        },
        "lifecycle": {"completion": completion, "steps": lifecycle_steps},
    }


def _mark_first_message_sent() -> None:
    with _workspace_lock:
        _workspace_state["lifecycle"]["first_message_sent"] = True
        _persist_workspace_state()


def _sweep_stale_tmp_files(max_age_hours: float = 2.0) -> int:
    """Delete /tmp/g4l_* files older than max_age_hours. Returns count deleted."""
    import glob as _glob
    import time as _time
    cutoff = _time.time() - max_age_hours * 3600
    deleted = 0
    for path in _glob.glob("/tmp/g4l_*"):
        try:
            if os.path.getmtime(path) < cutoff:
                os.unlink(path)
                deleted += 1
        except OSError:
            pass
    return deleted


@asynccontextmanager
async def lifespan(app: FastAPI):
    swept = _sweep_stale_tmp_files()
    if swept:
        import logging as _logging
        _logging.getLogger("chat_ui").info("startup: swept %d stale /tmp/g4l_* files", swept)

    upload_store.init()
    automation_store.init()
    workflow_store.init()
    job_queue.init()
    threading.Thread(
        # ornith:9b dropped 2026-07-10: conversation folding moved to a cloud
        # OpenRouter model (unreliable in a head-to-head bench), no local hot
        # path left to warm for it.
        target=lambda: ollama_warmup([_OLLAMA_DEFAULT_MODEL]),
        daemon=True,
    ).start()
    threading.Thread(target=warm_classifier, daemon=True).start()

    # Seed builtin workflow instances (idempotent)
    workflow_store.seed_builtins()

    # Workflow dispatching is handled by the background task worker
    # (python -m jobs.worker), not by the web UI process.
    # The worker polls both job_queue and workflow_store in a single loop.
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    account = _current_account(request)
    cta_href = "/app" if account else "/login"
    cta_label = "Open app" if account else "Sign in locally"
    html = (
        _load_template("landing.html")
        .replace("__PRIMARY_CTA_HREF__", cta_href)
        .replace("__PRIMARY_CTA_LABEL__", cta_label)
    )
    return HTMLResponse(html)


@app.get("/login")
def login_page(request: Request):
    if _current_account(request):
        return RedirectResponse(url="/app", status_code=303)
    return HTMLResponse(_load_template("login.html"))


@app.get("/app", response_class=HTMLResponse)
def app_shell(request: Request):
    if not _current_account(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(_load_template("index.html"))


@app.get("/agent/traces", response_class=HTMLResponse)
def traces_dashboard(request: Request):
    _require_auth(request)
    return HTMLResponse(_load_template("traces.html"))


@app.get("/favicon.ico")
def favicon():
    pixel = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+kZ0kAAAAASUVORK5CYII="
    )
    return Response(content=pixel, media_type="image/png")


@app.get("/history/{chat_id}")
def history(chat_id: str, request: Request):
    _require_auth(request)
    return conv_get(chat_id)


@app.delete("/history/{chat_id}")
def delete_history(chat_id: str, request: Request):
    _require_auth(request)
    conv_clear(chat_id)
    return {"cleared": True}


@app.get("/api/whatsapp-qr")
def whatsapp_qr(request: Request):
    import httpx

    _require_auth(request)
    bridge_url = os.environ.get("WHATSAPP_BRIDGE_URL", "http://localhost:9235")
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{bridge_url}/api/qr-json")
            if resp.status_code == 200:
                data = resp.json()
                return {"status": data.get("status"), "qr": data.get("qr")}
    except Exception:
        pass
    return {"status": "error", "detail": "Bridge unavailable"}


@app.get("/api/workspace")
def workspace_state(request: Request):
    return _build_workspace_payload(_current_account(request))


@app.post("/webhooks/trigger/{workflow_id}")
async def webhook_trigger(workflow_id: str, request: Request, secret: str = ""):
    """Inbound receiver for trigger_type='webhook' automations.
    Supports HMAC headers (X-Hub-Signature-256, X-Hub-Signature, Stripe-Signature)
    and legacy ?secret= query param fallback.
    """
    workflow_store.init()
    instance = workflow_store.get_workflow_instance(workflow_id)
    if not instance or instance.get("trigger_type") != "webhook" or instance.get("status") != "active":
        raise HTTPException(404, "Not found")

    webhook_secret = (instance.get("config") or {}).get("_webhook_secret", "")
    if not webhook_secret:
        raise HTTPException(404, "Not found")

    sig256 = request.headers.get("X-Hub-Signature-256")
    sig1 = request.headers.get("X-Hub-Signature")
    stripe_sig = request.headers.get("Stripe-Signature")
    if sig256 or sig1 or stripe_sig:
        body = await request.body()
        valid = False
        if sig256:
            expected = "sha256=" + hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
            valid = secrets.compare_digest(expected, sig256)
        elif sig1:
            expected = "sha1=" + hmac.new(webhook_secret.encode(), body, hashlib.sha1).hexdigest()
            valid = secrets.compare_digest(expected, sig1)
        elif stripe_sig:
            parts = {}
            for item in stripe_sig.split(","):
                k, _, v = item.strip().partition("=")
                parts[k] = v
            expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
            valid = secrets.compare_digest(expected, parts.get("v1", ""))
        if not valid:
            raise HTTPException(404, "Not found")
    else:
        if not secret or not secrets.compare_digest(secret, webhook_secret):
            raise HTTPException(404, "Not found")

    from jobs import handler_registry
    from jobs.handlers import user_automation as _ua
    handler_registry.register("user.automation", _ua.handle)
    result = handler_registry.dispatch(instance)
    workflow_store.update_workflow_instance(
        workflow_id, last_run_at=_now_iso(), last_result=result
    )
    workflow_store.append_run_history(workflow_id, result if isinstance(result, dict) else {"result": result})
    return {"status": "ok", "result": result}


@app.post("/api/auth/register")
def auth_register(req: LocalRegisterRequest, response: Response):
    if req.password and len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    with _workspace_lock:
        email = req.email.strip().lower()
        if not email:
            raise HTTPException(400, "Email is required")
        existing = _workspace_state.get("account")
        # Account takedown guard: once an account is configured (has a password
        # hash), a different email cannot overwrite it via register. A fresh /
        # unconfigured account (empty password_hash) can still be claimed by the
        # first caller — that's the legitimate first-run setup path.
        if (
            existing
            and existing.get("email")
            and existing.get("password_hash")
            and existing["email"].lower() != email
        ):
            raise HTTPException(409, "An account already exists for this workspace")
        display_name = req.display_name.strip() or "Local User"
        workspace_name = req.workspace_name.strip() or "Local Workspace"
        role = req.role.strip() or "Owner"
        if req.password:
            salt_hex, password_hash = _hash_password(req.password)
        else:
            salt_hex, password_hash = "", ""
        _workspace_state["account"] = {
            "email": email,
            "display_name": display_name,
            "workspace_name": workspace_name,
            "role": role,
            "avatar": _avatar_for(display_name),
            "password_salt": salt_hex,
            "password_hash": password_hash,
            "created_at": _now_iso(),
            "last_login_at": _now_iso(),
        }
        _persist_workspace_state()
        account = deepcopy(_workspace_state["account"])
    _set_session_cookie(response, email)
    return _build_workspace_payload(account)


@app.post("/api/auth/login")
def auth_login(req: LocalLoginRequest, response: Response):
    with _workspace_lock:
        account = deepcopy(_workspace_state.get("account"))
        if not account:
            raise HTTPException(404, "No local account configured")
        email = req.email.strip().lower()
        if email != account["email"].lower():
            raise HTTPException(401, "Invalid email or password")
        if not account.get("password_hash"):
            # No password configured — require explicit setup via DEV_PASSWORD /
            # register instead of silently accepting any password.
            raise HTTPException(401, "No password configured for this account")
        if not _verify_password(req.password, account["password_salt"], account["password_hash"]):
            raise HTTPException(401, "Invalid email or password")
        _workspace_state["account"]["last_login_at"] = _now_iso()
        _persist_workspace_state()
        account = deepcopy(_workspace_state["account"])
    _set_session_cookie(response, account["email"])
    return _build_workspace_payload(account)


@app.post("/api/auth/reset-password")
def auth_reset_password(req: LocalResetPasswordRequest, response: Response):
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    with _workspace_lock:
        account = _workspace_state.get("account")
        if not account:
            raise HTTPException(404, "No local account configured")
        email = req.email.strip().lower()
        if email != account["email"].lower():
            raise HTTPException(401, "Email does not match local account")
        salt_hex, password_hash = _hash_password(req.password)
        _workspace_state["account"]["password_salt"] = salt_hex
        _workspace_state["account"]["password_hash"] = password_hash
        _workspace_state["account"]["last_login_at"] = _now_iso()
        _persist_workspace_state()
        account = deepcopy(_workspace_state["account"])
    _set_session_cookie(response, account["email"])
    return _build_workspace_payload(account)


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    _clear_session_cookie(response)
    return _build_workspace_payload()


@app.post("/api/mock/login")
def workspace_login(req: MockLoginRequest, response: Response):
    raise HTTPException(410, "Use /api/auth/register or /api/auth/login")


@app.post("/api/mock/logout")
def workspace_logout(response: Response):
    return auth_logout(response)


@app.post("/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    chat_id: str = Form(default="web_ui"),
):
    _require_auth(request)
    # Reject oversized uploads BEFORE reading the body into RAM — file.read()
    # buffers the whole payload, so a multi-GB upload would OOM the process
    # before the 50MB check ran (live risk class: /chat/upload-audio already
    # streams chunks with a size cap; this one didn't).
    MAX_UPLOAD_BYTES = 50 * 1024 * 1024
    if file.size and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 50 MB)")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 50 MB)")
    file_id = str(uuid.uuid4())
    original_name = file.filename or "upload"
    mime = file.content_type or ""
    if not mime or mime == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(original_name)
        mime = guessed or "application/octet-stream"
    ext = Path(original_name).suffix
    dest_dir = upload_store.bucket_dir(mime=mime, ext=ext)
    stored_path = dest_dir / f"{file_id}{ext}"
    stored_path.write_bytes(content)
    bucket = upload_store._category(mime, ext)
    if bucket == "image":
        bucket = "images"
    upload_store.save(
        file_id=file_id,
        original_name=original_name,
        stored_path=str(stored_path),
        mime_type=mime,
        size_bytes=len(content),
        chat_id=chat_id,
        bucket=bucket,
    )
    return {"id": file_id, "name": original_name, "mime": mime, "size": len(content)}


def _make_preamble_filter():
    _PREAMBLE = (
        "okay,",
        "okay ",
        "hmm,",
        "hmm ",
        "let me ",
        "first,",
        "first ",
        "wait,",
        "wait ",
        "the user ",
        "so, ",
        "alright",
        "well,",
        "well ",
        "now,",
        "now ",
        "right,",
        "checks ",
        "checking",
        "thinks ",
        "thinking",
        "planning",
        "mental",
        "notes:",
        "note to",
        "recall",
        "remember",
        "considering",
        "analyzing",
        "reviewing",
        "i ",
    )
    _DETECT_CHARS = 60
    state = {"phase": "buffer", "buf": ""}

    def filter_token(token: str) -> str:
        if state["phase"] == "pass":
            return token
        state["buf"] += token
        if state["phase"] == "buffer":
            check = state["buf"].lower().lstrip()
            if check and any(w.startswith(check) for w in _PREAMBLE):
                if len(state["buf"]) < _DETECT_CHARS:
                    return ""
            if any(check.startswith(w) for w in _PREAMBLE):
                state["phase"] = "collect"
                return ""
            out = state["buf"]
            state["buf"] = ""
            state["phase"] = "pass"
            return out
        return ""

    _REASONING_STARTERS = (
        "okay",
        "hmm",
        "let me",
        "first,",
        "first ",
        "wait,",
        "wait ",
        "i need",
        "i should",
        "i'll",
        "the user",
        "so, ",
        "alright",
        "well,",
        "well ",
        "now,",
        "now ",
        "right,",
        "looking at",
        "thinking",
        "planning",
        "step ",
        "analysis:",
        "note:",
        "i will",
        "i ",
    )

    def _find_answer_start(buf: str) -> Optional[int]:
        i = 0
        while True:
            idx = buf.find("\n\n", i)
            if idx == -1:
                return None
            next_para = buf[idx + 2 :].lstrip()
            if not any(next_para.lower().startswith(w) for w in _REASONING_STARTERS):
                return idx + 2
            i = idx + 2

    def flush() -> str:
        if state["phase"] == "pass":
            return ""
        buf = state["buf"]
        state["buf"] = ""
        if state["phase"] == "buffer":
            return buf
        start = _find_answer_start(buf)
        return buf[start:].lstrip() if start is not None else buf

    return filter_token, flush


def _make_think_filter():
    _OPEN, _CLOSE = "<think>", "</think>"
    state = {"in_think": False, "buf": ""}

    def filter_token(token: str) -> str:
        state["buf"] += token
        out = ""
        while True:
            if state["in_think"]:
                idx = state["buf"].find(_CLOSE)
                if idx == -1:
                    for i in range(1, len(_CLOSE)):
                        if state["buf"].endswith(_CLOSE[:i]):
                            state["buf"] = state["buf"][-i:]
                            return out
                    state["buf"] = ""
                    return out
                state["in_think"] = False
                state["buf"] = state["buf"][idx + len(_CLOSE) :]
            else:
                idx = state["buf"].find(_OPEN)
                if idx == -1:
                    for i in range(1, len(_OPEN)):
                        if state["buf"].endswith(_OPEN[:i]):
                            out += state["buf"][:-i]
                            state["buf"] = state["buf"][-i:]
                            return out
                    out += state["buf"]
                    state["buf"] = ""
                    return out
                out += state["buf"][:idx]
                state["in_think"] = True
                state["buf"] = state["buf"][idx + len(_OPEN) :]

    def flush() -> str:
        remaining = state["buf"] if not state["in_think"] else ""
        state["buf"] = ""
        state["in_think"] = False
        return remaining

    return filter_token, flush


def _make_visible_token_filter():
    _think_filter, _think_flush = _make_think_filter()
    _preamble_filter, _preamble_flush = _make_preamble_filter()

    def filter_token(token: str) -> str:
        after_think = _think_filter(token)
        return _preamble_filter(after_think) if after_think else ""

    def flush() -> str:
        tail = _think_flush()
        visible_tail = _preamble_filter(tail) if tail else ""
        return visible_tail + _preamble_flush()

    return filter_token, flush


@app.post("/voice/process")
async def voice_process(
    request: Request,
    file: UploadFile = File(...),
    chat_id: str = Form(default="web_ui"),
    tts: str = Form(default="0"),
    web_search: str = Form(default="0"),
):
    _require_auth(request)
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty audio")
    use_tts = tts == "1"
    use_web_search = web_search == "1"

    async def generate():
        suffix = Path(file.filename or "voice.webm").suffix or ".webm"
        _fd, _tmp = tempfile.mkstemp(suffix=suffix)
        os.close(_fd)
        tmp = Path(_tmp)
        try:
            tmp.write_bytes(content)

            def _transcribe():
                from tools.audio import execute as transcribe

                return transcribe(str(tmp))

            transcript = await asyncio.to_thread(_transcribe)
        finally:
            tmp.unlink(missing_ok=True)
        bad = (
            "File not found",
            "ffmpeg",
            "Whisper",
            "Transcription",
            "[Could not",
            "[Binary",
            "[BLANK_AUDIO]",
        )
        if not transcript or any(transcript.startswith(p) for p in bad):
            yield f"data: {json.dumps({'type': 'error', 'text': transcript or 'Transcription failed'})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'transcript', 'text': transcript})}\n\n"
        token_q: queue_mod.Queue = queue_mod.Queue()
        result: dict = {}
        abort_event = register_active_stream(chat_id)

        def on_token(t: str):
            token_q.put(t)

        def run_llm():
            try:
                reply, model, intent = harness_run(
                    transcript, chat_id=chat_id, on_token=on_token, force_web_search=use_web_search
                )
                result.update(model=model, reply=reply)
                _mark_first_message_sent()
            except QueryAbortedException:
                _log.info("[voice/process] request chat_id=%s was cancelled by client", chat_id)
            except Exception as exc:
                result["error"] = str(exc)
            finally:
                token_q.put(None)
                with _active_streams_lock:
                    if _active_streams.get(chat_id) is abort_event:
                        _active_streams.pop(chat_id, None)

        t = threading.Thread(target=run_llm, daemon=True)
        t.abort_event = abort_event
        t.start()
        sent_buf = ""
        loop = asyncio.get_running_loop()
        _visible_filter, _visible_flush = _make_visible_token_filter()
        try:
            while True:
                try:
                    token = await loop.run_in_executor(None, lambda: token_q.get(timeout=300))
                except queue_mod.Empty:
                    result["error"] = "Model response timed out (>300s)."
                    break
                if token is None:
                    tail = _visible_flush()
                    if tail:
                        yield f"data: {json.dumps({'type': 'token', 'text': tail})}\n\n"
                        sent_buf += tail
                    if use_tts and sent_buf.strip():
                        wav = await asyncio.to_thread(_synthesize, sent_buf.strip())
                        if wav:
                            yield f"data: {json.dumps({'type': 'audio', 'data': base64.b64encode(wav).decode()})}\n\n"
                    break
                visible = _visible_filter(token)
                if visible:
                    yield f"data: {json.dumps({'type': 'token', 'text': visible})}\n\n"
                if use_tts:
                    sent_buf += visible if visible else ""
                    ready, sent_buf = _sentence_boundary(sent_buf)
                    if ready:
                        wav = await asyncio.to_thread(_synthesize, ready)
                        if wav:
                            yield f"data: {json.dumps({'type': 'audio', 'data': base64.b64encode(wav).decode()})}\n\n"
        finally:
            abort_event.set()

        if "error" in result:
            yield f"data: {json.dumps({'type': 'error', 'text': result['error']})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'done', 'model': result.get('model', '')})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/transcribe")
async def transcribe_audio(request: Request, file: UploadFile = File(...)):
    _require_auth(request)
    content = await file.read()
    suffix = Path(file.filename or "voice.webm").suffix or ".webm"
    _fd, _tmp = tempfile.mkstemp(suffix=suffix)
    os.close(_fd)
    tmp = Path(_tmp)
    try:
        tmp.write_bytes(content)

        def _run():
            from tools.audio import execute as transcribe

            return transcribe(str(tmp))

        text = await asyncio.to_thread(_run)
        return {"text": text}
    finally:
        tmp.unlink(missing_ok=True)


@app.get("/file/{file_id}")
def serve_file(file_id: str, request: Request):
    _require_auth(request)
    rec = upload_store.get(file_id)
    if not rec:
        raise HTTPException(404, "File not found")
    p = Path(rec["stored_path"])
    if not p.exists():
        raise HTTPException(404, "File missing from disk")
    return FileResponse(str(p), media_type=rec["mime_type"], filename=rec["original_name"])


@app.get("/view/chart/{filename}")
def serve_chart_html(filename: str, request: Request):
    _require_auth(request)
    charts_dir = Path(__file__).parent / "static" / "charts"
    p = charts_dir / filename
    if not p.exists() or p.suffix != ".html":
        raise HTTPException(404, "Chart not found")
    return FileResponse(str(p), media_type="text/html")


@app.get("/files/{chat_id}")
def list_files(chat_id: str, request: Request):
    _require_auth(request)
    return upload_store.list_for_chat(chat_id)


@app.delete("/file/{file_id}")
def delete_file(file_id: str, request: Request):
    _require_auth(request)
    upload_store.delete(file_id)
    return {"ok": True}


@app.post("/tts/speak")
async def tts_speak(req: TTSSpeakRequest, request: Request):
    _require_auth(request)
    wav = await asyncio.to_thread(_synthesize, req.text, req.voice)
    if not wav:
        raise HTTPException(status_code=500, detail="TTS failed")
    return Response(content=wav, media_type="audio/wav")


@app.post("/tts/speak/stream")
async def tts_speak_stream(req: TTSSpeakRequest, request: Request):
    _require_auth(request)
    async def generate():
        sentences = _split_sentences(_strip_md(req.text))
        for sentence in sentences:
            wav = await asyncio.to_thread(_synthesize_cached, sentence, req.voice)
            if wav:
                yield f"data: {json.dumps({'type': 'audio', 'data': base64.b64encode(wav).decode()})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _preview_text(text: str, n: int = 80) -> str:
    """Truncate text for log lines."""
    return text[:n] + "..." if len(text) > n else text


def _build_file_context(ids: list) -> str:
    """Return a text preamble built from uploaded file content for the given IDs."""
    if not ids:
        return ""
    parts = []
    text_buckets = {"pdf", "documents", "spreadsheets", "code", "data", "text", "other"}
    for fid in ids:
        rec = upload_store.get(fid)
        if not rec:
            continue
        name = rec.get("original_name", fid)
        extracted = rec.get("extracted_text")
        if extracted:
            parts.append(f"\n[File: {name}]\n{extracted}\n")
        elif rec.get("bucket") in text_buckets:
            try:
                content = Path(rec["stored_path"]).read_text(errors="replace")
                parts.append(f"\n[File: {name}]\n{content}\n")
            except Exception:
                pass
    return "".join(parts)


def _collect_image_b64(ids: list) -> list | None:
    """Return a list of base64-encoded image data URIs for image file IDs, or None if none."""
    result = []
    for fid in ids:
        rec = upload_store.get(fid)
        if not rec or rec.get("bucket") != "images":
            continue
        try:
            data = Path(rec["stored_path"]).read_bytes()
            mime = rec.get("mime_type", "image/png")
            result.append(f"data:{mime};base64,{base64.b64encode(data).decode()}")
        except Exception:
            pass
    return result if result else None


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    _require_auth(request)
    _log.info("chat request chat_id=%s message=%r", req.chat_id, _preview_text(req.message))
    # Bound the non-streaming path — previously asyncio.to_thread with no
    # timeout held the request open forever if the harness hung (dead Ollama,
    # stuck tool). 300s matches the streaming path's stall window.
    try:
        reply, model, intent = await asyncio.wait_for(
            asyncio.to_thread(harness_run, req.message, None, None, req.chat_id),
            timeout=300,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "Request timed out after 300s")
    _mark_first_message_sent()
    return {"reply": reply, "model": model, "intent": intent}


@app.get("/chat/stream")
async def chat_stream(
    request: Request,
    message: str,
    chat_id: str = "web_ui",
    file_ids: str = "",
    root: str = "",
    web_search: str = "0",
    local_agent: str = "0",
    plan_mode: str = "0",
    model: str = "",
):
    _require_auth(request)
    token_queue: queue_mod.Queue = queue_mod.Queue()
    result: dict = {}
    ids = [i for i in file_ids.split(",") if i.strip()] if file_ids else []
    full_message = _build_file_context(ids) + message
    images = _collect_image_b64(ids)

    abort_event = register_active_stream(chat_id)

    def on_token(t: str):
        token_queue.put(t)

    def run_harness():
        try:
            reply, routed_model, intent = harness_run(
                full_message,
                chat_id=chat_id,
                on_token=on_token,
                project_root=root or None,
                model=model or None,
                force_web_search=web_search == "1",
                force_local_agent=local_agent == "1",
                force_plan_mode=plan_mode == "1",
                images=images or None,
                manual_model_pick=bool(model),
            )
            result.update(model=routed_model, intent=intent, reply=reply)
            _mark_first_message_sent()
        except QueryAbortedException:
            _log.info("[chat/stream] request chat_id=%s was cancelled by client", chat_id)
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            token_queue.put(None)
            with _active_streams_lock:
                if _active_streams.get(chat_id) is abort_event:
                    _active_streams.pop(chat_id, None)

    t = threading.Thread(target=run_harness, daemon=True)
    t.abort_event = abort_event
    t.start()

    async def generate():
        loop = asyncio.get_event_loop()
        _visible_filter, _visible_flush = _make_visible_token_filter()
        streamed_any = False
        _stall_pings = 0
        try:
            while True:
                try:
                    token = await loop.run_in_executor(None, lambda: token_queue.get(timeout=300))
                except queue_mod.Empty:
                    # 2026-08-03: a genuinely slow round (bash_exec up to 300s,
                    # a cloud model trickling with no on_token) previously got a
                    # spurious {'done':True,'error':'timeout'} at exactly 300s of
                    # silence. Ping once to keep the client alive; only give up
                    # after two consecutive 300s stalls.
                    _stall_pings += 1
                    if _stall_pings < 2:
                        yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                        continue
                    yield f"data: {json.dumps({'done': True, 'error': 'timeout'})}\n\n"
                    return
                _stall_pings = 0
                if token is None:
                    tail = _visible_flush()
                    if tail:
                        streamed_any = True
                        yield f"data: {json.dumps({'token': tail})}\n\n"
                    if "error" in result:
                        yield f"data: {json.dumps({'error': result['error']})}\n\n"
                    else:
                        # Some paths (e.g. websearch fallbacks) return a final reply
                        # without streaming tokens. Emit it so the UI never goes blank.
                        reply = result.get("reply") or ""
                        if not streamed_any and reply:
                            yield f"data: {json.dumps({'token': reply})}\n\n"
                        yield f"data: {json.dumps({'done': True, 'model': result.get('model', ''), 'intent': result.get('intent', '')})}\n\n"
                    return
                if not isinstance(token, str):
                    token = token.content if hasattr(token, 'content') else str(token)
                visible = _visible_filter(token)
                if visible:
                    streamed_any = True
                    yield f"data: {json.dumps({'token': visible})}\n\n"
        finally:
            abort_event.set()

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/chat/abort")
def abort_chat(chat_id: str = "", thread_id: str = "", request: Request = None):
    """Abort an active stream by chat_id or thread_id. Called by frontend stop button."""
    _require_auth(request)
    target = chat_id or thread_id
    if target:
        abort_active_stream(target)
    return {"ok": True}


@app.get("/search/stream")
async def search_stream(
    request: Request,
    message: str,
    thread_id: str = "",
    file_ids: str = "",
):
    _require_auth(request)
    ids = [i for i in file_ids.split(",") if i.strip()] if file_ids else []
    full_message = _build_file_context(ids) + message

    # Guard check — thread_id implies conversation continuity, so a deictic
    # follow-up ("tell me more about that") shouldn't get an unwanted
    # clarifying question here when it wouldn't in the main chat window
    # (harness.py threads has_history the same way for /chat/stream).
    has_history = bool(conv_get(thread_id)) if thread_id else False
    clarification = _guard_check(full_message, has_history=has_history)
    if clarification:
        async def generate_clarification():
            yield f"data: {json.dumps({'type': 'done', 'reply': f'Please clarify: {clarification}', 'model': 'websearch'})}\n\n"
        return StreamingResponse(generate_clarification(), media_type="text/event-stream")

    queue = queue_mod.Queue()
    abort_event = register_active_stream(thread_id)

    def on_token(tok: str):
        queue.put(tok)

    def _run():
        try:
            answer = _websearch(query=full_message, on_token=on_token)
        except QueryAbortedException:
            _log.info("[search/stream] request thread_id=%s was cancelled by client", thread_id)
        except Exception as exc:
            _log.error("[search/stream] error: %s", exc)
        finally:
            queue.put(None)  # sentinel
            with _active_streams_lock:
                if _active_streams.get(thread_id) is abort_event:
                    _active_streams.pop(thread_id, None)

    thread = threading.Thread(target=_run, daemon=True)
    thread.abort_event = abort_event
    thread.start()

    async def generate():
        try:
            while True:
                # 2026-08-03: plain queue.get() had NO timeout — a stalled
                # websearch thread never enqueues the None sentinel, and the SSE
                # connection hung forever. 300s bound matches chat_stream; on
                # timeout emit a done event so the client can close.
                try:
                    tok = await asyncio.get_event_loop().run_in_executor(None, lambda: queue.get(timeout=300))
                except queue_mod.Empty:
                    yield f"data: {json.dumps({'type': 'done', 'reply': '', 'model': 'websearch', 'error': 'search timed out'})}\n\n"
                    break
                if tok is None:
                    break
                yield f"data: {json.dumps({'type': 'token', 'text': tok})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'reply': '', 'model': 'websearch'})}\n\n"
        finally:
            abort_event.set()

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/search/history/{thread_id}")
async def search_history(thread_id: str, request: Request):
    _require_auth(request)
    return {"thread_id": thread_id, "history": []}


@app.get("/models/hot")
def models_hot(request: Request):
    _require_auth(request)
    try:
        import ollama as _ollama

        ps = _ollama.ps()
        return {"hot": [m.model.removesuffix(":latest") for m in (ps.models or [])]}
    except Exception:
        return {"hot": []}


@app.get("/chat/context-usage")
def chat_context_usage(chat_id: str, model: str = "deepseek/deepseek-v4-flash", request: Request = None):
    _require_auth(request)
    from clients.router import MODEL_SPECS, _tok

    history = conv_get(chat_id) or []
    used = sum(_tok(t.get("content", "")) for t in history)
    limit, _reserved = MODEL_SPECS.get(model, (32768, 1024))
    return {
        "used": used,
        "limit": limit,
        "pct": round(min(used / limit, 1.0) * 100, 1),
        "model": model,
    }


@app.get("/health/ollama")
def health_ollama(request: Request):
    _require_auth(request)
    host = (
        os.environ.get("OLLAMA_HOST")
        or os.environ.get("OLLAMA_BASE_URL")
        or "http://localhost:11434"
    )
    tags_url = host.rstrip("/") + "/api/tags"
    errors = []
    try:
        import requests as _req

        r = _req.get(tags_url, timeout=2)
        if r.ok:
            return {
                "status": "ok",
                "models": [m["name"].removesuffix(":latest") for m in r.json().get("models", [])],
            }
    except Exception as exc:
        errors.append(str(exc))
    try:
        r = subprocess.run(
            ["curl", "-sS", tags_url], capture_output=True, text=True, timeout=3, check=True
        )
        return {
            "status": "ok",
            "models": [
                m["name"].removesuffix(":latest") for m in json.loads(r.stdout).get("models", [])
            ],
        }
    except Exception as exc:
        errors.append(str(exc))
    joined = " | ".join(errors).lower()
    if any(marker in joined for marker in ("operation not permitted", "permission denied", "blocked")):
        return {
            "status": "blocked",
            "models": [],
            "detail": "Cannot reach local Ollama from this environment.",
        }
    return {"status": "down", "models": []}


@app.post("/health/ollama/restart")
def restart_ollama(request: Request):
    _require_auth(request)
    from tools.notifications import push as _notify

    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["open", "-a", "Ollama"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        _notify("Ollama restart requested.", level="info", source="ollama_health")
        return {"launched": True}
    except Exception as e:
        return {"launched": False, "error": str(e)}


@app.get("/notifications")
def get_notifications(unread_only: bool = False, request: Request = None):
    _require_auth(request)
    from tools.notifications import list_all, unread_count

    return {"notifications": list_all(unread_only=unread_only), "unread": unread_count()}


@app.post("/notifications/{notif_id}/read")
def mark_notification_read(notif_id: str, request: Request = None):
    _require_auth(request)
    from tools.notifications import mark_read

    return {"ok": mark_read(notif_id)}


@app.delete("/notifications/{notif_id}")
def delete_notification(notif_id: str, request: Request = None):
    _require_auth(request)
    from tools.notifications import dismiss

    return {"ok": dismiss(notif_id)}


@app.delete("/notifications")
def clear_notifications(request: Request = None):
    _require_auth(request)
    from tools.notifications import dismiss_all

    return {"cleared": dismiss_all()}


@app.get("/approvals")
def get_approvals(status: str = "", limit: int = 50, request: Request = None):
    _require_auth(request)
    workflow_store.init()
    return {"approvals": workflow_store.list_approval_requests(status=status, limit=limit)}


@app.post("/approvals")
def create_approval(body: ApprovalCreateRequest, request: Request = None):
    _require_auth(request)
    workflow_store.init()
    approval = workflow_store.create_approval_request(
        kind=body.kind,
        payload=body.payload,
        workflow_instance_id=body.workflow_instance_id,
        job_id=body.job_id,
        expires_at=body.expires_at,
    )
    from tools.notifications import push

    push(
        message=f"Approval requested: {body.kind}",
        level="warning",
        source="approval",
        action_label="Review",
        action_url=f"/approvals/{approval['id']}",
        action_type="approval",
        action_payload={"approval_id": approval["id"], "kind": body.kind},
    )
    return approval


@app.post("/approvals/{approval_id}/approve")
def approve_approval(approval_id: str, request: Request = None):
    _require_auth(request)
    workflow_store.init()
    return workflow_store.resolve_approval_request(approval_id, "approved") or HTTPException(404)


@app.post("/approvals/{approval_id}/reject")
def reject_approval(approval_id: str, request: Request = None):
    _require_auth(request)
    workflow_store.init()
    return workflow_store.resolve_approval_request(approval_id, "rejected") or HTTPException(404)


@app.get("/fs/git-branch")
def fs_git_branch(path: str = "", request: Request = None):
    _require_auth(request)
    p = Path(path).expanduser().resolve() if path else Path.home()
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(p),
            timeout=5,
        )
        return {"branch": r.stdout.strip() if r.returncode == 0 else ""}
    except Exception:
        return {"branch": ""}


@app.get("/fs/tree")
def fs_tree(path: str = "", depth: int = 2, request: Request = None):
    _require_auth(request)
    from pathlib import Path as P

    root = P(path).expanduser().resolve() if path else P.home()

    def _walk(p: P, max_depth: int, cur_depth: int):
        items = []
        try:
            entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except (PermissionError, OSError):
            return items
        for e in entries:
            if e.name.startswith(".") and e.name not in {".env", ".gitignore"}:
                continue
            if e.name in {
                "__pycache__",
                "node_modules",
                ".git",
                "venv",
                ".venv",
                "dist",
                "build",
                ".next",
            }:
                continue
            item = {"name": e.name, "path": str(e), "type": "dir" if e.is_dir() else "file"}
            if e.is_dir() and cur_depth < max_depth:
                item["children"] = _walk(e, max_depth, cur_depth + 1)
            items.append(item)
        return items

    return {"root": str(root), "items": _walk(root, depth, 0)}


@app.get("/fs/read")
def fs_read(path: str, request: Request = None):
    _require_auth(request)
    p = Path(path).expanduser().resolve()
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > 200_000:
            content = content[:200_000] + "\n...(truncated)"
        return {"path": str(p), "content": content}
    except Exception as e:
        return {"error": str(e)}


@app.post("/fs/write")
def fs_write(body: FSWrite, request: Request = None):
    _require_auth(request)
    p = Path(body.path).expanduser().resolve()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body.content, encoding="utf-8")
        return {"ok": True, "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/fs/run")
def fs_run(body: FSRun, request: Request = None):
    _require_auth(request)
    p = Path(body.path).expanduser().resolve()
    try:
        ext = p.suffix.lower()
        if ext == ".py":
            from tools.repl import run_python

            output = run_python(p.read_text(errors="replace"), timeout=60)
        else:
            from tools.shell import bash_exec

            cmd = (
                f"bash {p}"
                if ext in {".sh", ".bash"}
                else (f"node {p}" if ext == ".js" else str(p))
            )
            output = bash_exec(cmd, cwd=str(p.parent), timeout=60)
        return {"output": output}
    except Exception as e:
        return {"output": f"[error] {e}"}


@app.post("/ide/index")
async def ide_index(req: IdeIndexRequest, request: Request = None):
    _require_auth(request)
    root = req.path.strip()

    def _run():
        try:
            from tools.semantic_files import index_directory

            index_directory(root)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "indexing", "path": root}


@app.get("/automations")
async def get_automation_catalog(request: Request = None):
    _require_auth(request)
    presets = automation_store.list_presets()
    automations = list_automations()
    for a in automations:
        if a["id"] in presets:
            a["params"].update(presets[a["id"]])
    return {"automations": automations}


@app.post("/automations/{automation_id}/preset")
async def save_automation_preset(automation_id: str, body: AutomationPresetRequest, request: Request = None):
    _require_auth(request)
    automation = get_automation(automation_id)
    if automation is None:
        raise HTTPException(404, "Automation not found")
    params = dict(automation.get("params", {}))
    params.update(body.params or {})
    automation_store.save_preset(automation_id, params)
    return {"status": "saved"}


@app.get("/automations/runs")
async def get_automation_runs(limit: int = 20, request: Request = None):
    _require_auth(request)
    catalog = list_automations()
    task_names = [i["task_name"] for i in catalog]
    runs = job_queue.list_jobs(limit=limit, task_names=task_names)
    task_to_id = {i["task_name"]: i["id"] for i in catalog}
    for r in runs:
        r["automation_id"] = task_to_id.get(r["task_name"], "")
    return {"runs": runs}


@app.post("/automations/{automation_id}/run")
async def run_automation(automation_id: str, body: AutomationRunRequest, request: Request = None):
    _require_auth(request)
    automation = get_automation(automation_id)
    if automation is None:
        raise HTTPException(404, "Automation not found")
    payload = build_dispatch_payload(automation_id, body.overrides)
    return {
        "job_id": job_queue.enqueue(payload["task_name"], payload["params"]),
        "status": "queued",
    }


@app.get("/workflows")
async def get_workflows(automation_id: str = "", status: str = "", limit: int = 50, request: Request = None):
    _require_auth(request)
    workflow_store.init()
    return {
        "workflows": workflow_store.list_workflow_instances(
            automation_id=automation_id, status=status, limit=limit
        )
    }


@app.post("/workflows")
async def create_workflow(body: WorkflowCreateRequest, request: Request = None):
    _require_auth(request)
    workflow_store.init()
    return workflow_store.create_workflow_instance(
        automation_id=body.automation_id,
        task_name=body.task_name or get_automation(body.automation_id)["task_name"],
        config=body.config,
        schedule=body.schedule,
        status=body.status,
        next_run_at=body.next_run_at,
        dedupe_key=body.dedupe_key,
    )


@app.post("/workflows/{workflow_id}/pause")
async def pause_workflow(workflow_id: str, request: Request = None):
    _require_auth(request)
    workflow_store.init()
    return workflow_store.pause_workflow_instance(workflow_id) or HTTPException(404)


@app.post("/workflows/{workflow_id}/resume")
async def resume_workflow(workflow_id: str, request: Request = None):
    _require_auth(request)
    workflow_store.init()
    return workflow_store.resume_workflow_instance(workflow_id) or HTTPException(404)


@app.post("/workflows/{workflow_id}/run-now")
async def run_workflow_now(workflow_id: str, request: Request = None):
    _require_auth(request)
    workflow_store.init()
    return workflow_store.trigger_workflow_instance(workflow_id) or HTTPException(404)


@app.post("/tasks/dispatch")
async def dispatch_task(body: TaskDispatchRequest, request: Request = None):
    _require_auth(request)
    if body.task_name not in TASK_ROUTING:
        raise HTTPException(400)
    return {"job_id": job_queue.enqueue(body.task_name, body.params), "status": "queued"}


@app.get("/tasks/{job_id}")
async def get_task_status(job_id: str, request: Request = None):
    _require_auth(request)
    job = job_queue.get_job(job_id)
    return job or HTTPException(404)


# ── Skills Hub API ─────────────────────────────────────────────────────────


@app.get("/skills")
async def get_skills(category: str = "", request: Request = None):
    """List all available skills, optionally filtered by category."""
    _require_auth(request)
    return {
        "skills": _list_skills(category),
        "categories": _list_categories(),
    }


@app.get("/skills/match")
async def match_skills(query: str = "", request: Request = None):
    """Auto-match a user query to the best skill."""
    _require_auth(request)
    if not query:
        raise HTTPException(400, "query required")
    skill = _match_skill(query)
    if skill is None:
        return {"match": None, "message": "No matching skill found. Try being more specific."}
    return {
        "match": {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "category": skill.category,
            "tools": skill.tools,
            "max_rounds": skill.max_rounds,
            "icon": skill.icon,
        }
    }


@app.post("/skills/dispatch")
async def dispatch_skill(request: Request, body: SkillDispatchRequest):
    """Dispatch a skill synchronously. Returns the agent's response."""
    _require_auth(request)
    if not body.message:
        raise HTTPException(400, "message required")
    try:
        result, model, _ = _dispatch_skill(
            skill_id=body.skill_id,
            user_message=body.message,
            chat_id=body.chat_id,
        )
        return {"result": result, "model": model, "skill": body.skill_id}
    except KeyError:
        raise HTTPException(404, f"Unknown skill: {body.skill_id!r}")


@app.get("/skills/dispatch/stream")
async def dispatch_skill_stream(
    request: Request,
    skill_id: str = "",
    message: str = "",
    chat_id: str = "skills-ui",
):
    """Dispatch a skill with SSE streaming."""
    _require_auth(request)
    if not skill_id or not message:
        raise HTTPException(400, "skill_id and message required")

    _known = _list_skills_map()
    if not any(s["id"] == skill_id for s in _known):
        raise HTTPException(404, f"Unknown skill: {skill_id!r}")

    abort_event = register_active_stream(chat_id)

    async def generate():
        loop = asyncio.get_event_loop()
        q = queue_mod.Queue()

        def on_token(token: str):
            q.put(token)

        def run_skill():
            try:
                result, model, _ = _dispatch_skill(
                    skill_id=skill_id,
                    user_message=message,
                    chat_id=chat_id,
                    on_token=on_token,
                )
            except QueryAbortedException:
                _log.info("[skills/dispatch] request chat_id=%s was cancelled by client", chat_id)
            except Exception as e:
                q.put(f"\n[error] {e}")
            finally:
                q.put(None)
                with _active_streams_lock:
                    if _active_streams.get(chat_id) is abort_event:
                        _active_streams.pop(chat_id, None)

        t = threading.Thread(target=run_skill, daemon=True)
        t.abort_event = abort_event
        t.start()

        try:
            while True:
                try:
                    item = await loop.run_in_executor(None, lambda: q.get(timeout=300))
                    if item is None:
                        break
                    yield f"data: {json.dumps({'type': 'token', 'data': {'content': item}})}\n\n"
                except queue_mod.Empty:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        finally:
            abort_event.set()

    return StreamingResponse(generate(), media_type="text/event-stream")


_skills_map_cache: list = []


def _list_skills_map() -> list:
    """Cached skill list for endpoint lookups."""
    global _skills_map_cache
    if not _skills_map_cache:
        _skills_map_cache = _list_skills()
    return _skills_map_cache


# ── Local Agent ────────────────────────────────────────────────────────────
class LocalAgentJob:
    """Track a local-agent job for long-running operations."""
    messages: list
    status: str  # "running", "done", "error"
    reply: str = ""
    error: str = ""


_local_agent_jobs: dict[str, LocalAgentJob] = {}
_local_agent_jobs_lock = threading.Lock()


@app.get("/chat/local-agent/stream")
async def chat_local_agent_stream(
    request: Request,
    message: str = "",
    chat_id: str = "local-agent-ui",
    model: str = DEFAULT_CLOUD_MODEL,
    file_ids: str = "",
):
    """Run a query through the LangGraph local-agent with SSE streaming."""
    import os as _os
    if not _os.environ.get("G4L_DEV_MODE"):
        _require_auth(request)

    if not message:
        raise HTTPException(400, "message required")

    ids = [i for i in file_ids.split(",") if i.strip()] if file_ids else []
    full_message = _build_file_context(ids) + message
    abort_event = register_active_stream(chat_id)

    async def generate():
        """Generate SSE events from LangGraph stream."""
        loop = asyncio.get_event_loop()
        queue = queue_mod.Queue()

        def on_event(event_type: str, data: dict):
            """Callback for graph streaming events."""
            if event_type == "specialist_start":
                queue.put({"type": "specialist_start", "data": data})
            elif event_type == "specialist_done":
                queue.put({"type": "specialist_done", "data": data})
            elif event_type == "tool_call":
                queue.put({"type": "tool_call", "data": data})
            elif event_type == "tool_result":
                queue.put({"type": "tool_result", "data": data})
            elif event_type == "final":
                queue.put({"type": "final", "data": {"content": data}})
                queue.put(None)  # Signal end

        def run_graph():
            try:
                from agents.local_agent_graph import run_local_agent_streaming
                from clients.ollama_client import check_aborted
                for event_type, data in run_local_agent_streaming(
                    query=full_message,
                    model=model,
                    chat_id=chat_id,
                ):
                    check_aborted()
                    on_event(event_type, data)
            except QueryAbortedException:
                _log.info("[chat/local-agent] request chat_id=%s was cancelled by client", chat_id)
            except Exception as e:
                queue.put({"type": "error", "data": {"error": str(e)}})
                queue.put(None)
            finally:
                with _active_streams_lock:
                    if _active_streams.get(chat_id) is abort_event:
                        _active_streams.pop(chat_id, None)

        t = threading.Thread(target=run_graph, daemon=True)
        t.abort_event = abort_event
        t.start()

        try:
            while True:
                try:
                    item = await loop.run_in_executor(None, lambda: queue.get(timeout=300))
                    if item is None:
                        break
                    yield f"data: {json.dumps(item)}\n\n"
                except queue_mod.Empty:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        finally:
            abort_event.set()

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/chat/local-agent/pending-confirmations")
async def local_agent_pending_confirmations(request: Request):
    """List bash_exec calls currently blocked awaiting human approve/deny."""
    import os as _os
    if not _os.environ.get("G4L_DEV_MODE"):
        _require_auth(request)
    from tools.confirmation import list_pending
    return {"pending": list_pending()}


@app.post("/chat/local-agent/confirm")
async def local_agent_confirm(request: Request):
    """Approve or deny a pending bash_exec/github_* confirmation (tools/confirmation.py).
    Runs the real tool call synchronously on approval and returns its result."""
    import os as _os
    if not _os.environ.get("G4L_DEV_MODE"):
        _require_auth(request)
    body = await request.json()
    token = body.get("token", "")
    approved = bool(body.get("approved", False))
    if not token:
        raise HTTPException(400, "token required")
    from tools.registry import execute_confirmed
    result = execute_confirmed(token, approved)
    if result.startswith("[error] unknown or already-resolved"):
        raise HTTPException(404, result)
    return {"token": token, "approved": approved, "result": result}


@app.get("/local-agent/{job_id}")
async def get_local_agent_status(job_id: str, request: Request = None):
    """Get local-agent job status and result."""
    _require_auth(request)
    with _local_agent_jobs_lock:
        job = _local_agent_jobs.get(job_id)
        if not job:
            raise HTTPException(404)
        return {
            "status": job.status,
            "reply": job.reply,
            "error": job.error,
        }


@app.get("/agent/trace/{run_id}")
def get_agent_trace(run_id: str, request: Request):
    """Return the structured tool-call trace for a completed local-agent run."""
    _require_auth(request)
    from agents.local_agent_graph import get_trace
    trace = get_trace(run_id)
    if trace is None:
        raise HTTPException(404, f"No trace found for run_id={run_id!r}")
    return {"run_id": run_id, "trace": trace}


@app.get("/agent/traces/recent")
def get_recent_traces(
    request: Request,
    tool: str | None = None,
    has_error: bool | None = None,
    limit: int = 50,
):
    """Live view over recent subagent/tool runs — same query_traces() the
    internal query_recent_traces tool already uses, just exposed over HTTP for
    a polling dashboard instead of only being LLM-callable."""
    _require_auth(request)
    from store import trace_store
    runs = trace_store.query_traces(tool=tool, has_error=has_error, limit=limit)
    return {
        "runs": [
            {**trace_store.summarize_run(r["run_id"]), "entries": r["entries"]}
            for r in runs
        ]
    }


@app.get("/agent/routing/recent")
def get_recent_routing_decisions(request: Request, limit: int = 50):
    """Live view over model_for_intent()'s recent decisions (pinned/normal/
    budget_blocked/unhealthy_fallback) — the trace dashboard above shows what
    tools ran, this shows why a request landed on local vs cloud."""
    _require_auth(request)
    from clients import routing_stats
    return {"decisions": routing_stats.recent_decisions(limit=limit)}


@app.post("/voice/ingest")
async def voice_ingest(request: Request):
    """Brabble hook POSTs voice query+reply here. Pushes to all SSE listeners."""
    _require_auth(request)
    body = await request.json()
    query = (body.get("query") or "").strip()
    reply = (body.get("reply") or "").strip()
    if not query:
        return {"ok": False, "error": "empty query"}
    _log.info("[voice] ingest: query=%r reply=%r listeners=%d", query[:60], reply[:60], len(_voice_queues))
    event = json.dumps({"type": "voice", "query": query, "reply": reply})
    with _voice_queues_lock:
        dead: list[asyncio.Queue] = []
        for q in _voice_queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            _voice_queues.discard(q)
    return {"ok": True}


@app.get("/voice/debug")
async def voice_debug(request: Request):
    """Return voice debug info."""
    _require_auth(request)
    with _voice_queues_lock:
        listener_count = len(_voice_queues)
    return {"listeners": listener_count}


@app.get("/voice/stream")
async def voice_stream(request: Request):
    """SSE endpoint for Brabble voice wake-word events pushed via /voice/ingest."""
    _require_auth(request)
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    with _voice_queues_lock:
        _voice_queues.add(q)

    async def generate():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {event}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        finally:
            with _voice_queues_lock:
                _voice_queues.discard(q)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/voice/status")
async def voice_status(request: Request):
    """Check if voiceprint is enrolled and return verification state."""
    _require_auth(request)
    import os as _os
    vp_path = _os.path.expanduser("~/.config/g4l/voiceprint/voiceprint.npy")
    return {"enrolled": _os.path.exists(vp_path)}


@app.post("/voice/enroll")
async def voice_enroll(request: Request, file: UploadFile = File(...)):
    """Receive a WAV upload, extract speaker embedding, save voiceprint."""
    _require_auth(request)
    import os as _os
    import tempfile as _tmp
    import numpy as _np
    content = await file.read()
    fd, path = _tmp.mkstemp(suffix=".wav", prefix="g4l_vp_")
    _os.close(fd)
    with open(path, "wb") as f:
        f.write(content)
    try:
        from tools.voiceprint import enroll as _vp_enroll
        emb = _vp_enroll(path)
        out = _os.path.expanduser("~/.config/g4l/voiceprint/voiceprint.npy")
        _os.makedirs(_os.path.dirname(out), exist_ok=True)
        _np.save(out, emb)
        return {"ok": True, "dim": int(emb.shape[0])}
    except Exception as e:
        raise HTTPException(400, f"Enrollment failed: {e}")
    finally:
        _os.unlink(path)


# --- Odysseus UI/UX Integration APIs ---
_running_sidecars = {}

@app.get("/api/chat/compare/models")
def get_compare_models(request: Request):
    _require_auth(request)
    try:
        import ollama
        r = ollama.list()
        models = [m.model for m in r.models]
        return {"models": models}
    except Exception as e:
        return {"models": ["gemma4:e2b", "qwen3.5:9b", "qwen3:4b"], "error": str(e)}

@app.post("/api/chat/compare/vote")
def record_compare_vote(req: dict, request: Request):
    _require_auth(request)
    vote_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": req.get("prompt", ""),
        "model_a": req.get("model_a", ""),
        "model_b": req.get("model_b", ""),
        "vote": req.get("vote", "")
    }
    stats_file = Path(__file__).parent / "data" / "compare_stats.json"
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    
    votes = []
    if stats_file.exists():
        try:
            votes = json.loads(stats_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    votes.append(vote_data)
    stats_file.write_text(json.dumps(votes, indent=2), encoding="utf-8")
    return {"ok": True}

@app.get("/chat/compare/stream")
async def chat_compare_stream(
    request: Request,
    message: str,
    model_a: str,
    model_b: str,
    chat_id: str = "web_ui",
    file_ids: str = "",
    root: str = "",
):
    _require_auth(request)
    token_queue = queue_mod.Queue()
    ids = [i for i in file_ids.split(",") if i.strip()] if file_ids else []
    full_message = _build_file_context(ids) + message
    images = _collect_image_b64(ids)
    abort_event = register_active_stream(chat_id)

    def run_harness_model(pane: str, model_name: str):
        try:
            harness_run(
                full_message,
                chat_id=chat_id,
                on_token=lambda t: token_queue.put((pane, t)),
                project_root=root or None,
                model=model_name,
                images=images or None,
                manual_model_pick=True,
            )
        except QueryAbortedException:
            _log.info("[chat/compare] request chat_id=%s model=%s was cancelled by client", chat_id, model_name)
        except Exception as exc:
            token_queue.put((pane, f"Error: {exc}"))
        finally:
            token_queue.put((pane, None))

    t_a = threading.Thread(target=run_harness_model, args=("A", model_a), daemon=True)
    t_a.abort_event = abort_event
    t_a.start()

    t_b = threading.Thread(target=run_harness_model, args=("B", model_b), daemon=True)
    t_b.abort_event = abort_event
    t_b.start()

    async def generate():
        loop = asyncio.get_event_loop()
        done_a = False
        done_b = False
        _visible_filter_a, _visible_flush_a = _make_visible_token_filter()
        _visible_filter_b, _visible_flush_b = _make_visible_token_filter()
        
        try:
            while not (done_a and done_b):
                try:
                    pane, token = await loop.run_in_executor(None, lambda: token_queue.get(timeout=300))
                except queue_mod.Empty:
                    yield f"data: {json.dumps({'done': True, 'error': 'timeout'})}\n\n"
                    return
                
                if token is None:
                    if pane == "A":
                        done_a = True
                        tail = _visible_flush_a()
                    else:
                        done_b = True
                        tail = _visible_flush_b()
                    if tail:
                        yield f"data: {json.dumps({'pane': pane, 'token': tail})}\n\n"
                    yield f"data: {json.dumps({'pane': pane, 'done': True})}\n\n"
                else:
                    if not isinstance(token, str):
                        token = token.content if hasattr(token, 'content') else str(token)
                    visible = _visible_filter_a(token) if pane == "A" else _visible_filter_b(token)
                    if visible:
                        yield f"data: {json.dumps({'pane': pane, 'token': visible})}\n\n"
        finally:
            abort_event.set()
            with _active_streams_lock:
                if _active_streams.get(chat_id) is abort_event:
                    _active_streams.pop(chat_id, None)

    return StreamingResponse(generate(), media_type="text/event-stream")

@app.get("/api/research/reports")
def list_research_reports(request: Request):
    _require_auth(request)
    reports_dir = Path(__file__).parent.parent / "data" / "research_reports"
    if not reports_dir.exists():
        return {"reports": []}
    files = []
    for f in reports_dir.glob("*.md"):
        files.append({
            "filename": f.name,
            "size": f.stat().st_size,
            "modified": f.stat().st_mtime,
        })
    files.sort(key=lambda x: x["modified"], reverse=True)
    return {"reports": files}

@app.get("/api/research/reports/{filename}")
def get_research_report(filename: str, request: Request):
    _require_auth(request)
    reports_dir = Path(__file__).parent.parent / "data" / "research_reports"
    file_path = reports_dir / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "Report file not found")
    return {"filename": filename, "content": file_path.read_text(encoding="utf-8")}

@app.get("/api/system/profile")
def get_system_profile(request: Request):
    _require_auth(request)
    try:
        from scripts.profile_hardware import get_cpu_info, get_ram_gb, get_gpu_vram_gb, get_recommendation
        from scripts.serve_sidecar import DEFAULT_GGUFS, MODELS_DIR
        cpu = get_cpu_info()
        ram = get_ram_gb()
        gpu, vram = get_gpu_vram_gb()
        rec = get_recommendation(ram, gpu, vram)
        active_sidecars = list(_running_sidecars.keys())
        downloaded_models = []
        for m_name, m_meta in DEFAULT_GGUFS.items():
            if (MODELS_DIR / m_meta["file"]).exists():
                downloaded_models.append(m_name)
        return {
            "cpu": cpu,
            "ram": f"{ram:.1f} GB",
            "gpu": f"{gpu} ({vram:.1f} GB VRAM)",
            "tier": rec["tier"],
            "primary": rec["primary"],
            "suggested_models": rec["models"],
            "suggested_ctx": rec["ctx"],
            "active_sidecars": active_sidecars,
            "downloaded_models": downloaded_models,
        }
    except Exception as e:
        raise HTTPException(500, f"Profiling failed: {e}")

@app.post("/api/system/models/download")
def trigger_model_download(req: dict, request: Request):
    _require_auth(request)
    model_name = req.get("model_name")
    if not model_name:
        raise HTTPException(400, "Missing model_name")
    from scripts.serve_sidecar import download_model
    threading.Thread(target=download_model, args=(model_name,), daemon=True).start()
    return {"ok": True, "detail": f"Started background download of {model_name}"}

@app.post("/api/system/serve/toggle")
def toggle_sidecar_server(req: dict, request: Request):
    _require_auth(request)
    model_name = req.get("model_name")
    port = req.get("port", 8000)
    ctx = req.get("ctx", 8192)
    if not model_name:
        raise HTTPException(400, "Missing model_name")
    if model_name in _running_sidecars:
        proc = _running_sidecars.pop(model_name)
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return {"ok": True, "status": "stopped"}
    else:
        from scripts.serve_sidecar import find_llama_server, DEFAULT_GGUFS, MODELS_DIR
        binary = find_llama_server()
        if not binary:
            raise HTTPException(400, "llama-server binary not found. Install it first (e.g. brew install llama.cpp)")
        filename = DEFAULT_GGUFS[model_name]["file"]
        model_path = MODELS_DIR / filename
        if not model_path.exists():
            raise HTTPException(400, "Model file not found. Download it first!")
        cmd = [
            binary,
            "--model", str(model_path),
            "--port", str(port),
            "--ctx-size", str(ctx),
            "--ngl", "-1"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _running_sidecars[model_name] = proc
        return {"ok": True, "status": "started", "port": port}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9234, log_level="warning")
