#!/usr/bin/env python3
"""Warm Chatterbox Multilingual TTS daemon for the ringback container."""
from __future__ import annotations

import io
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import soundfile as sf
import perth
import torch

_log = logging.getLogger("chatterbox_daemon")
_PORT = int(os.environ.get("CHATTERBOX_TTS_PORT", "9241"))
_REFERENCE_WAV = os.environ.get("CHATTERBOX_REFERENCE_WAV", "").strip()
_model = None
_lock = threading.Lock()


class _NoopWatermark:
    def apply_watermark(self, wav, sample_rate):
        return wav


perth.PerthImplicitWatermarker = _NoopWatermark
from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # noqa: E402


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                _device = os.environ.get("CHATTERBOX_DEVICE", "").strip() or (
                    "mps" if torch.backends.mps.is_available() else "cpu"
                )
                _log.info("loading Chatterbox Multilingual on %s", _device)
                _model = ChatterboxMultilingualTTS.from_pretrained(device=_device)
                if _REFERENCE_WAV:
                    if not os.path.isfile(_REFERENCE_WAV):
                        raise FileNotFoundError(f"voice reference not found: {_REFERENCE_WAV}")
                    _log.info("loading voice reference: %s", _REFERENCE_WAV)
                    _model.prepare_conditionals(_REFERENCE_WAV)
                _log.info("Chatterbox Multilingual ready")
    return _model


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            return
        self.send_error(404)

    def do_POST(self):  # noqa: N802
        if self.path != "/synthesize":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            text = str(body.get("text", "")).strip()
            language = str(body.get("language", "fr")).strip() or "fr"
            if not text:
                raise ValueError("text is required")
            model = _get_model()
            with _lock:
                audio = model.generate(text, language_id=language)
            buf = io.BytesIO()
            sf.write(buf, audio.squeeze().detach().cpu().numpy(), model.sr, format="WAV")
            payload = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            _log.exception("synthesis failed")
            self.send_error(500, str(exc))

    def log_message(self, fmt, *args):
        _log.info("%s", fmt % args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[chatterbox] %(message)s")
    server = ThreadingHTTPServer(("0.0.0.0", _PORT), _Handler)
    _log.info("listening on :%d", _PORT)
    server.serve_forever()
