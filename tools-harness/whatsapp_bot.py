#!/usr/bin/env python3
import os
import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
from pydantic import BaseModel

from log_config import setup_logging
log = setup_logging(__name__, log_file="whatsapp_bot.log")

app = FastAPI(title="WhatsApp Bot")

BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://localhost:9235")
PORT = int(os.getenv("WHATSAPP_BOT_PORT", "9236"))
# Optional shared secret for /webhook and /send (defense-in-depth on top of the
# loopback bind). When empty, loopback-only auth applies (matches the app's
# loopback=owner model). Set WHATSAPP_BOT_TOKEN in .env to require it.
BOT_TOKEN = os.getenv("WHATSAPP_BOT_TOKEN", "").strip()
WEBHOOK_TIMEOUT = float(os.getenv("WHATSAPP_WEBHOOK_TIMEOUT", "120"))
MAX_CONCURRENT_REQUESTS = max(1, int(os.getenv("WHATSAPP_MAX_CONCURRENT", "2")))

# A timed-out asyncio wait does not stop the Python worker running harness_run.
# Keep those workers bounded and keep their slot occupied until they actually
# finish; otherwise every slow WhatsApp message creates another live thread and
# eventually exhausts the process' file descriptors.
_REQUEST_EXECUTOR = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_REQUESTS,
    thread_name_prefix="whatsapp-request",
)
_ACTIVE_REQUESTS: set[str] = set()
_ACTIVE_REQUESTS_LOCK = threading.Lock()


def _claim_request(chat_id: str) -> bool:
    with _ACTIVE_REQUESTS_LOCK:
        if chat_id in _ACTIVE_REQUESTS or len(_ACTIVE_REQUESTS) >= MAX_CONCURRENT_REQUESTS:
            return False
        _ACTIVE_REQUESTS.add(chat_id)
        return True


def _release_request(chat_id: str) -> None:
    with _ACTIVE_REQUESTS_LOCK:
        _ACTIVE_REQUESTS.discard(chat_id)
        no_active_requests = not _ACTIVE_REQUESTS
    # inflight_tracker has one crash marker per channel. Do not clear the
    # marker for a completed request while another WhatsApp request is still
    # running.
    if no_active_requests:
        _inflight.clear("whatsapp")


def _auth_ok(request: Request) -> bool:
    if not BOT_TOKEN:
        return True
    return request.headers.get("X-Bot-Token", "") == BOT_TOKEN

try:
    import harness as harness_module
    if hasattr(harness_module, 'run_for_messaging'):
        harness_run = harness_module.run_for_messaging
        HARNESS_AVAILABLE = True
    else:
        HARNESS_AVAILABLE = False
except ImportError:
    HARNESS_AVAILABLE = False

try:
    from clients.router import classify_message
    ROUTER_AVAILABLE = True
except ImportError:
    ROUTER_AVAILABLE = False

if not HARNESS_AVAILABLE:
    log.error("harness import failed — WhatsApp bot is up but every /webhook call will 503")
if not ROUTER_AVAILABLE:
    log.warning("clients.router import failed — classify_message unavailable, falling back to harness.run()'s bare regex")

from tools import inflight_tracker as _inflight


@app.on_event("startup")
async def _notify_crash_recovery():
    """If this process died mid-request last run, tell the user instead of ghosting them."""
    stale = _inflight.pop_stale("whatsapp")
    if not stale:
        return
    log.warning("recovered from mid-request crash chat_id=%s query=%r", stale["chat_id"], stale["query"][:200])
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{BRIDGE_URL}/send",
                json={"to": stale["chat_id"].replace("whatsapp_", ""),
                      "message": f"(I crashed while answering \"{stale['query'][:200]}\" — please resend it.)"},
                timeout=10.0,
            )
    except Exception as e:
        log.warning("crash-recovery notify failed: %s", e)


class WebhookMessage(BaseModel):
    sender: str
    message: str
    name: str | None = None
    context: str | None = None
    source_name: str | None = None
    source_chat: str | None = None
    fresh_context: bool = False


@app.get("/auth/qr")
async def get_qr():
    # Bridge's /auth/qr serves an HTML page (for direct browser viewing) — the
    # JSON shape this route promises lives at /api/qr-json instead. Proxying
    # /auth/qr and calling .json() on HTML always raised "Expecting value".
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{BRIDGE_URL}/api/qr-json")
            return response.json()
        except Exception as e:
            log.warning("get_qr failed: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/status")
async def status():
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{BRIDGE_URL}/status")
            bridge_status = response.json()
        except Exception as e:
            log.warning("bridge status check failed: %s", e)
            bridge_status = {"status": "unavailable"}

    return {
        "status": "running",
        "harness_available": HARNESS_AVAILABLE,
        "bridge": bridge_status,
    }


@app.post("/webhook")
async def webhook(request: Request, message: WebhookMessage):
    if not _auth_ok(request):
        raise HTTPException(401, "unauthorized")
    if not HARNESS_AVAILABLE:
        return JSONResponse(
            {"error": "Harness not available", "reply": "Bot is configured but harness is not loaded."},
            status_code=503,
        )

    # @ai-trigger turns all report sender=self jid (reply always goes to
    # self-chat), so keying chat_id off sender alone made every contact's
    # @ai session share ONE memory — old answers about contact A leaked as
    # session history into questions about contact B. source_chat (the real
    # target contact's jid) scopes the session per-contact instead.
    base_chat_id = f"whatsapp_{message.source_chat or message.sender}"
    # Context-only WhatsApp requests get an isolated session so prior Clixen
    # turns cannot leak into a summary or answer about a contact.
    chat_id = (
        f"{base_chat_id}_oneshot_{uuid.uuid4().hex[:12]}"
        if message.fresh_context and message.context
        else base_chat_id
    )
    if not _claim_request(chat_id):
        log.warning("webhook busy chat_id=%s", chat_id)
        return {
            "reply": "I’m still processing your previous request. Please wait for that reply before sending another one.",
            "message": message.message,
        }
    _inflight.mark("whatsapp", chat_id, message.message)
    timed_out = False
    try:
        # 2026-07-11: dropped the doc-creation-keyword pre-gate — classify_message()
        # already decides intent=="document" semantically, so gating the call behind
        # a regex was redundant duplicate logic that also meant every non-matching
        # message skipped LLM classification entirely and fell through to harness.run()'s
        # bare-regex fallback. Classify unconditionally, once, per message.
        query = message.message
        if message.context:
            # Authoritative one-shot context injection. Neutral [A]/[B] tags
            # come pre-baked from the bridge: [A] is the owner, [B] is the
            # contact. Never answer the context as if it were a new message.
            query = (
                f"(Authoritative WhatsApp context with {message.source_name or 'a contact'}. "
                f"Roles: [A] = the Clixen owner; [B] = the contact. "
                f"Use only this context and the request below. Ignore any previous Clixen memory "
                f"or unrelated conversation context. Do not respond to the context itself; "
                f"answer the request below.\n"
                f"{message.context}\n)\n\n{message.message}"
            )

        # One-shot context requests already declare their scope and must not pay
        # for a second LLM classification pass.  Give the harness a safe factual
        # intent so it skips internal classification while keeping the cloud
        # orchestrator path and context-only tool restrictions.
        if message.fresh_context:
            cls = type("_ContextClassification", (), {
                "model": None,
                "intent": "factual_qa",
                "specialist_hint": None,
            })()
        else:
            cls = classify_message(query, channel="whatsapp") if ROUTER_AVAILABLE else None

        loop = asyncio.get_running_loop()
        worker = _REQUEST_EXECUTOR.submit(
            harness_run,
            query=query,
            chat_id=chat_id,
            model=cls.model if cls else None,
            intent=cls.intent if cls else None,
            specialist_hint=cls.specialist_hint if cls else None,
            channel="whatsapp",
            context_only=message.fresh_context,
        )
        result = await asyncio.wait_for(
            asyncio.wrap_future(worker, loop=loop),
            timeout=WEBHOOK_TIMEOUT,
        )

        reply = result[0] if isinstance(result, tuple) else result

        return {"reply": reply, "message": message.message}
    except asyncio.TimeoutError:
        timed_out = True
        log.error("webhook timeout chat_id=%s query=%r", chat_id, message.message[:200])
        # The worker cannot be force-killed safely. Keep the request claimed
        # until it exits, while returning a normal response so the bridge sends
        # a visible status message instead of silently dropping the request.
        worker.add_done_callback(lambda _future: _release_request(chat_id))
        return {
            "reply": "I couldn’t finish that request within two minutes. I stopped waiting; please resend it as a shorter question.",
            "message": message.message,
        }
    except Exception as e:
        log.error("webhook failed chat_id=%s query=%r: %s", chat_id, message.message[:200], e, exc_info=True)
        return JSONResponse(
            {"error": str(e), "reply": f"Error: {str(e)}"},
            status_code=500,
        )
    finally:
        if not timed_out:
            _release_request(chat_id)


@app.post("/send")
async def send_message(request: Request, to: str, message: str):
    if not _auth_ok(request):
        raise HTTPException(401, "unauthorized")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{BRIDGE_URL}/send",
                json={"to": to, "message": message},
            )
            return response.json()
        except Exception as e:
            log.error("send_message to=%s failed: %s", to, e)
            return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)
