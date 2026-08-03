#!/usr/bin/env python3
"""Persistent Kokoro TTS daemon — a warm process brabble_hook.py borrows.

kokoro_onnx cold-loads in ~1.85s (import + 325MB ONNX model + voices file +
first inference) — fine for telegram_bot.py, which caches one Kokoro
instance for the life of the core.py process, but a non-starter for
brabble_hook.py, which execs cold on every single wake-word trigger by
design (see its own docstring — that's how it dodges harness.py's ~4.5s
import cost). This daemon eats that load cost once, at startup, and serves
synthesis over a tiny local HTTP endpoint so brabble_hook.py's per-utterance
cost stays near zero, same as it already is with `say`.

Run as its own core.py-supervised thread (see core.py's _TARGETS), not a
standalone script — same crash-restart semantics as chat_ui/task_worker.
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 9237
DEFAULT_VOICE = "af_heart"
# 0.0.0.0 default is deliberate: the ringback docker container reaches this via
# host.docker.internal (docker bridge, not loopback). Override to 127.0.0.1 when
# ringback isn't used. See _get_auth_token() for the optional token gate.
BIND_HOST = os.environ.get("KOKORO_BIND_HOST", "0.0.0.0")

log = logging.getLogger("kokoro_daemon")

_kokoro = None
_kokoro_lock = threading.Lock()


def _get_auth_token() -> str | None:
    """Optional shared secret. When set, /synthesize* requires an
    X-Auth-Token header matching it — closes the LAN-exposed TTS endpoint
    (0.0.0.0 bind) without breaking the ringback docker bridge. Clients that
    need it must send the same env value."""
    token = os.environ.get("KOKORO_AUTH_TOKEN", "").strip()
    return token or None


def _auth_ok(handler: BaseHTTPRequestHandler) -> bool:
    token = _get_auth_token()
    if not token:
        return True
    supplied = handler.headers.get("X-Auth-Token", "")
    return supplied == token


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        with _kokoro_lock:
            if _kokoro is None:
                from kokoro_onnx import Kokoro

                onnx_path = os.environ.get(
                    "KOKORO_ONNX_PATH",
                    str(Path(__file__).resolve().parent.parent / "models" / "kokoro-v1.0.onnx"),
                )
                voices_path = os.environ.get(
                    "KOKORO_VOICES_PATH",
                    str(Path(__file__).resolve().parent.parent / "models" / "voices-v1.0.bin"),
                )
                log.info("loading Kokoro model (onnx=%s)", onnx_path)
                _kokoro = Kokoro(onnx_path, voices_path)
                log.info("Kokoro loaded")
    return _kokoro


def _to_wav_bytes(samples, sample_rate: int) -> bytes:
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.request.settimeout(10)

    def log_message(self, fmt, *args):  # noqa: A002 — quiet, matches other services
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            try:
                self.wfile.write(b"ok")
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_response(404)
            self.end_headers()

    def _read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length)
        try:
            return json.loads(body or b"{}")
        except Exception:
            return None

    def do_POST(self):
        if not _auth_ok(self):
            self.send_response(401)
            self.end_headers()
            return
        if self.path == "/synthesize_stream":
            self._handle_stream()
            return
        if self.path != "/synthesize":
            self.send_response(404)
            self.end_headers()
            return
        payload = self._read_json_body()
        if payload is None:
            self.send_response(400)
            self.end_headers()
            return
        text = (payload.get("text") or "").strip()
        voice = payload.get("voice") or DEFAULT_VOICE
        if not text:
            self.send_response(400)
            self.end_headers()
            return
        try:
            samples, sample_rate = _get_kokoro().create(text, voice=voice, speed=1.0, lang="en-us")
            wav = _to_wav_bytes(samples, sample_rate)
        except Exception as e:
            log.warning("synthesis failed: %s", e)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav)))
        self.end_headers()
        try:
            self.wfile.write(wav)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle_stream(self):
        """Stream audio back chunk-by-chunk as kokoro_onnx's create_stream()
        generates them, instead of waiting for the whole utterance to finish
        synthesizing. Framing is a simple 4-byte big-endian length prefix per
        WAV chunk over a connection-close-terminated body (BaseHTTPRequestHandler
        defaults to HTTP/1.0, so no Content-Length/chunked-encoding juggling
        needed — the client just reads length-prefixed blocks until EOF).
        """
        import asyncio
        import struct

        payload = self._read_json_body()
        if payload is None:
            self.send_response(400)
            self.end_headers()
            return
        text = (payload.get("text") or "").strip()
        voice = payload.get("voice") or DEFAULT_VOICE
        if not text:
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

        async def _run():
            kokoro = _get_kokoro()
            async for audio_part, sample_rate in kokoro.create_stream(text, voice, speed=1.0, lang="en-us"):
                wav = _to_wav_bytes(audio_part, sample_rate)
                self.wfile.write(struct.pack(">I", len(wav)))
                self.wfile.write(wav)
                self.wfile.flush()

        try:
            asyncio.run(_run())
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-stream — nothing to clean up
        except Exception as e:
            log.warning("stream synthesis failed: %s", e)


def main():
    log.info("warming Kokoro TTS daemon on %s:%d", BIND_HOST, PORT)
    _get_kokoro()  # pay the load cost now, not on the first real request
    # BIND_HOST defaults to 0.0.0.0 (not 127.0.0.1) because the ringback docker
    # container reaches this via host.docker.internal, which arrives over the
    # Docker bridge/gateway interface, not loopback — a 127.0.0.1-bound socket
    # silently dropped every container request (found live, TTS timeouts during
    # a test call). When KOKORO_AUTH_TOKEN is set, LAN callers also need the
    # shared token. Set KOKORO_BIND_HOST=127.0.0.1 when ringback is not used.
    server = ThreadingHTTPServer((BIND_HOST, PORT), _Handler)
    log.info("Kokoro TTS daemon listening on %s:%d", BIND_HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    main()
