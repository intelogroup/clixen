#!/usr/bin/env python3
"""Warm Surya daemon — a persistent process holding Surya OCR (surya-ocr 0.22.x,
the structured-document VLM) loaded once, serving OCR over a tiny local HTTP
endpoint. Surya produces layout-aware output (HTML with <table>/<h1>/<li>…) so
scanned PDFs with tables/multi-column layout come back structured, not as a flat
text dump — which is why it won the clixen OCR benchmark over PaddleOCR/Tesseract.

Why a separate process (not a core.py thread):
  - Surya needs its OWN venv (python3.12+ and heavy deps: torch, llama.cpp
    bindings); the clixen venv must not carry that weight.
  - First inference pays a long llama.cpp warm-up; a persistent daemon pays it
    once instead of every tool call.

Endpoints:
  GET  /health        -> "ok"
  POST /ocr           -> {"image_path": str, "mode": "ocr"|"table"} (optional
                         max_tokens) -> {"text": "<structured html>", "elapsed_s": float}
Run as its own core.py-supervised subprocess thread (see core.py), gated on the
OCR venv actually being present so a machine without Surya stays clean.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("SURYA_PORT", "9240"))
BIND_HOST = os.environ.get("SURYA_BIND_HOST", "127.0.0.1")

log = logging.getLogger("surya_daemon")

_manager = None
_manager_lock = threading.Lock()


def _resolve_auth_token() -> str | None:
    token = os.environ.get("SURYA_AUTH_TOKEN", "").strip()
    return token or None


def _auth_ok(handler: BaseHTTPRequestHandler) -> bool:
    token = _resolve_auth_token()
    if not token:
        return True
    return handler.headers.get("X-Auth-Token", "") == token


def _get_manager():
    global _manager
    if _manager is not None:
        return _manager
    with _manager_lock:
        if _manager is not None:
            return _manager
        from surya.inference import get_default_manager

        t0 = time.time()
        _manager = get_default_manager()
        _manager.start()  # spawn the llama.cpp backend at startup, not on first call
        log.info("surya manager started in %.1fs", time.time() - t0)
        return _manager


def _run_ocr(image_path: str, mode: str = "ocr", max_tokens: int | None = None) -> str:
    from PIL import Image

    from surya.inference import BatchInputItem
    from surya.inference.schema import (
        PROMPT_TYPE_HIGH_ACCURACY_BBOX,
        PROMPT_TYPE_TABLE_REC,
    )

    manager = _get_manager()
    ptype = PROMPT_TYPE_TABLE_REC if mode == "table" else PROMPT_TYPE_HIGH_ACCURACY_BBOX
    image = Image.open(image_path).convert("RGB")
    item = BatchInputItem(image=image, prompt_type=ptype, max_tokens=max_tokens or 8192)
    t0 = time.time()
    out = manager.generate([item])
    elapsed = time.time() - t0
    log.info("surya %s %s in %.1fs", mode, image_path, elapsed)
    text = (out[0].raw or "").strip()
    if not text:
        return "No text detected in image."
    return text


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.request.settimeout(900)

    def log_message(self, fmt, *args):  # noqa: A002
        pass

    def _read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return None

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/health":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not _auth_ok(self):
            self._send(401, b"unauthorized", "text/plain")
            return
        if self.path != "/ocr":
            self._send(404, b"not found", "text/plain")
            return
        payload = self._read_json_body()
        if not payload or not payload.get("image_path"):
            self._send(400, b"missing image_path", "text/plain")
            return
        try:
            t0 = time.time()
            text = _run_ocr(
                payload["image_path"],
                mode=str(payload.get("mode", "ocr")),
                max_tokens=int(payload["max_tokens"]) if payload.get("max_tokens") else None,
            )
            self._send(200, json.dumps({"text": text, "elapsed_s": round(time.time() - t0, 2)}).encode())
        except Exception as e:
            log.warning("ocr failed: %s", e, exc_info=True)
            self._send(500, json.dumps({"error": str(e)}).encode())


def main():
    _get_manager()  # pay the load/warm-up cost at startup, not on the first request
    server = ThreadingHTTPServer((BIND_HOST, PORT), _Handler)
    log.info("Surya daemon listening on %s:%d", BIND_HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    main()
