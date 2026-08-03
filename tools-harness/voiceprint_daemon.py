#!/usr/bin/env python3
"""Persistent speaker-verification daemon — a warm process brabble_hook.py borrows.

Same shape and same reason as kokoro_daemon.py: `from resemblyzer import
VoiceEncoder` cold-imports in ~1.48s (measured), paid on every single
wake-word trigger since brabble_hook.py execs cold by design. This daemon
eats that load cost once, at startup, and serves verification over a tiny
local HTTP endpoint.

Run as its own core.py-supervised thread (see core.py's _TARGETS), not a
standalone script — same crash-restart semantics as chat_ui/task_worker.
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9238

log = logging.getLogger("voiceprint_daemon")


def _warm():
    from tools.voiceprint import _get_encoder

    log.info("loading Resemblyzer voice encoder")
    _get_encoder()
    log.info("Resemblyzer loaded")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A002 — quiet, matches other services
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/verify":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length)
        try:
            payload = json.loads(body or b"{}")
            audio_path = payload.get("audio_path") or ""
            voiceprint_path = payload.get("voiceprint_path") or ""
            threshold = float(payload.get("threshold", 0.55))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return
        if not audio_path or not voiceprint_path:
            self.send_response(400)
            self.end_headers()
            return
        try:
            from tools.voiceprint import verify_from_path

            match, similarity = verify_from_path(audio_path, voiceprint_path, threshold=threshold)
        except Exception as e:
            log.warning("verify failed: %s", e)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())
            return
        result = json.dumps({"match": bool(match), "similarity": similarity}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)


def main():
    log.info("warming voiceprint daemon on port %d", PORT)
    _warm()  # pay the load cost now, not on the first real request
    server = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    log.info("voiceprint daemon listening on 127.0.0.1:%d", PORT)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    main()
