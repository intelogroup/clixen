#!/usr/bin/env python3
"""Warm Kyutai Pocket TTS daemon.

pocket-tts (kyutai-labs/pocket-tts) is torch-based and cold-loads its model
weights (100M params) from Hugging Face on first use — plus it computes a
voice-conditioning state on first request. Both are slow, so this daemon eats
that cost once at startup and serves synthesis over a tiny local HTTP endpoint,
mirroring kokoro_daemon.py (thread-safety + health + auth-token gate).

Unlike kokoro (ONNX, in-process) this holds a torch model, so core.py runs it
as its OWN subprocess (see core.py's _run_pocket_tts_daemon), same isolation
pattern as the other local daemons. One model per process: the language is fixed at boot via
POCKET_TTS_LANGUAGE (default "english"); "alba" is the default voice, and the
`voice` field on /synthesize can be any named voice or a local wav/safetensors
path for voice cloning.

French requests (payload `"lang": "fr"`) route to a separate warm Piper voice
(fr_FR-siwis-medium, ONNX, CPU) instead of the Kyutai model — Kyutai's
pocket-tts is English-only, and Piper benchmarked ~6-20x faster than the other
French TTS options tested (VoxCPM, Qwen3-TTS, Supertonic-3) on this hardware.

Supertonic-3 is wired in as an opt-in alternate French engine (payload
`"lang": "fr", "engine": "supertonic"`) for when its voice is preferred over
Piper's — slower (~41s vs ~7s for 600 words) but different voice quality.
Runs at `total_steps=5` (default is 8): benchmarked 38% faster with no
audible quality loss on this hardware. Supertonic has no GPU/CoreML path
(CPU-only by design per its own docs), so `total_steps` is the only real
speed lever for it.
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

import numpy as np

PORT = int(os.environ.get("POCKET_TTS_PORT", "9242"))
DEFAULT_VOICE = os.environ.get("POCKET_TTS_VOICE", "alba").strip() or "alba"
LANGUAGE = os.environ.get("POCKET_TTS_LANGUAGE", "english").strip() or "english"
# 127.0.0.1 default: no docker consumer yet, keep it loopback-only.
# Override to 0.0.0.0 only if a container needs it (see KOKORO_BIND_HOST notes).
BIND_HOST = os.environ.get("POCKET_TTS_BIND_HOST", "127.0.0.1")
_MAX_VOICE_STATES = 8

_PIPER_FR_DIR = Path(__file__).parent / "models_local" / "piper"
PIPER_FR_MODEL = os.environ.get(
    "PIPER_FR_MODEL", str(_PIPER_FR_DIR / "fr_FR-siwis-medium.onnx")
)

SUPERTONIC_VOICE = os.environ.get("SUPERTONIC_VOICE", "M1").strip() or "M1"
SUPERTONIC_TOTAL_STEPS = int(os.environ.get("SUPERTONIC_TOTAL_STEPS", "5"))

log = logging.getLogger("pocket_tts_daemon")

_model = None
_model_lock = threading.Lock()
_gen_lock = threading.Lock()
_voice_states: dict[str, dict] = {}
_voice_lock = threading.Lock()

_piper_fr_voice = None
_piper_fr_lock = threading.Lock()

_supertonic_tts = None
_supertonic_style = None
_supertonic_lock = threading.Lock()


def _get_piper_fr_voice():
    global _piper_fr_voice
    if _piper_fr_voice is None:
        with _piper_fr_lock:
            if _piper_fr_voice is None:
                from piper import PiperVoice

                log.info("loading Piper French voice from %s", PIPER_FR_MODEL)
                _piper_fr_voice = PiperVoice.load(PIPER_FR_MODEL)
                log.info("Piper French voice loaded (sample_rate=%s)", _piper_fr_voice.config.sample_rate)
    return _piper_fr_voice


def _piper_synthesize_wav(text: str) -> bytes:
    voice = _get_piper_fr_voice()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(voice.config.sample_rate)
        with _gen_lock:  # onnxruntime session: keep one synth in flight at a time
            for chunk in voice.synthesize(text):
                pcm = (np.clip(chunk.audio_float_array, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                w.writeframes(pcm)
    return buf.getvalue()


def _get_supertonic():
    global _supertonic_tts, _supertonic_style
    if _supertonic_tts is None:
        with _supertonic_lock:
            if _supertonic_tts is None:
                from supertonic import TTS

                log.info("loading Supertonic-3 (voice=%s, total_steps=%d)", SUPERTONIC_VOICE, SUPERTONIC_TOTAL_STEPS)
                _supertonic_tts = TTS(auto_download=True)
                _supertonic_style = _supertonic_tts.get_voice_style(voice_name=SUPERTONIC_VOICE)
                log.info("Supertonic-3 loaded (sample_rate=%s)", _supertonic_tts.sample_rate)
    return _supertonic_tts, _supertonic_style


def _supertonic_synthesize_wav(text: str) -> bytes:
    tts, style = _get_supertonic()
    with _gen_lock:  # onnxruntime sessions: keep one synth in flight at a time
        wav, _duration = tts.synthesize(text, voice_style=style, lang="fr", total_steps=SUPERTONIC_TOTAL_STEPS)
    return _to_wav_bytes(wav, tts.sample_rate)


def _get_auth_token() -> str | None:
    token = os.environ.get("POCKET_TTS_AUTH_TOKEN", "").strip()
    return token or None


def _auth_ok(handler: BaseHTTPRequestHandler) -> bool:
    token = _get_auth_token()
    if not token:
        return True
    return handler.headers.get("X-Auth-Token", "") == token


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from pocket_tts import TTSModel

                log.info("loading Pocket TTS model (language=%s)", LANGUAGE)
                _model = TTSModel.load_model(language=LANGUAGE)
                log.info("Pocket TTS model loaded (sample_rate=%s)", _model.sample_rate)
    return _model


def _get_voice_state(voice: str) -> dict:
    with _voice_lock:
        state = _voice_states.get(voice)
    if state is not None:
        return state
    model = _get_model()
    log.info("computing voice state for %r", voice)
    state = model.get_state_for_audio_prompt(voice)
    with _voice_lock:
        if len(_voice_states) >= _MAX_VOICE_STATES:
            _voice_states.pop(next(iter(_voice_states)))
        _voice_states[voice] = state
    return state


def _to_wav_bytes(audio, sample_rate: int) -> bytes:
    a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
    if a.ndim == 2:
        a = a[0]
    pcm = (np.clip(a, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
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
        self.request.settimeout(30)

    def log_message(self, fmt, *args):  # noqa: A002
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

    def do_POST(self):
        if not _auth_ok(self):
            self.send_response(401)
            self.end_headers()
            return
        if self.path != "/synthesize":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            payload = None
        if not isinstance(payload, dict):
            self.send_response(400)
            self.end_headers()
            return
        text = (payload.get("text") or "").strip()
        voice = (payload.get("voice") or DEFAULT_VOICE).strip() or DEFAULT_VOICE
        lang = (payload.get("lang") or "").strip().lower()
        engine = (payload.get("engine") or "").strip().lower()
        if not text:
            self.send_response(400)
            self.end_headers()
            return
        is_fr = lang in ("fr", "french", "fr_fr", "fr-fr")
        try:
            if is_fr and engine == "supertonic":
                wav = _supertonic_synthesize_wav(text)
            elif is_fr:
                wav = _piper_synthesize_wav(text)
            else:
                model = _get_model()
                voice_state = _get_voice_state(voice)
                with _gen_lock:  # generate_audio is NOT thread-safe
                    audio = model.generate_audio(voice_state, text)
                wav = _to_wav_bytes(audio, model.sample_rate)
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


def main():
    log.info("warming Pocket TTS daemon on %s:%d", BIND_HOST, PORT)
    _get_model()  # pay the load cost now, not on the first real request
    _get_voice_state(DEFAULT_VOICE)
    _get_piper_fr_voice()
    _get_supertonic()
    server = ThreadingHTTPServer((BIND_HOST, PORT), _Handler)
    log.info("Pocket TTS daemon listening on %s:%d", BIND_HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    main()
