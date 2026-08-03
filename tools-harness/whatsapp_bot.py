#!/usr/bin/env python3
import os
import asyncio
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

    chat_id = f"whatsapp_{message.sender}"
    _inflight.mark("whatsapp", chat_id, message.message)
    try:
        # 2026-07-11: dropped the doc-creation-keyword pre-gate — classify_message()
        # already decides intent=="document" semantically, so gating the call behind
        # a regex was redundant duplicate logic that also meant every non-matching
        # message skipped LLM classification entirely and fell through to harness.run()'s
        # bare-regex fallback. Classify unconditionally, once, per message.
        cls = classify_message(message.message, channel="whatsapp") if ROUTER_AVAILABLE else None

        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: harness_run(
                    query=message.message,
                    chat_id=chat_id,
                    model=cls.model if cls else None,
                    intent=cls.intent if cls else None,
                    specialist_hint=cls.specialist_hint if cls else None,
                    channel="whatsapp",
                ),
            ),
            timeout=120.0,
        )

        reply = result[0] if isinstance(result, tuple) else result

        return {"reply": reply, "message": message.message}
    except asyncio.TimeoutError:
        log.error("webhook timeout chat_id=%s query=%r", chat_id, message.message[:200])
        return JSONResponse(
            {"error": "timeout", "reply": "Sorry, I took too long to respond. Please try again."},
            status_code=504,
        )
    except Exception as e:
        log.error("webhook failed chat_id=%s query=%r: %s", chat_id, message.message[:200], e, exc_info=True)
        return JSONResponse(
            {"error": str(e), "reply": f"Error: {str(e)}"},
            status_code=500,
        )
    finally:
        _inflight.clear("whatsapp")


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