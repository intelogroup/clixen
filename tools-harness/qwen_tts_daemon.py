#!/usr/bin/env python3
"""Warm Qwen3-TTS (mlx_audio) daemon — primary French TTS for ugent-app's tutor skill.

Loading Qwen/Qwen3-TTS-12Hz-1.7B-Base cold (weights + tokenizer + speech
tokenizer) takes ~8s, so this eats that cost once at startup and serves
synthesis over a tiny local HTTP endpoint, same pattern as kokoro_daemon.py
and pocket_tts_daemon.py.

Needed a one-time env fix to load at all: transformers 5.2.0 (as installed
in this venv) imports `is_offline_mode` from huggingface_hub, which was
dropped from huggingface_hub 0.35/0.36 ahead of the transformers 5.x cutover
that expects huggingface_hub>=1.3 — see
https://github.com/huggingface/transformers/issues/42757. Pinned this venv to
the known-compatible pair instead of jumping to hub>=1.3 (untested against
the rest of this codebase): transformers==4.57.3, huggingface_hub==0.36.2.

Base model has no preset voices (no CustomVoice speaker table downloaded) —
runs zero-shot via use_zero_spk_emb=True. Real benchmark on this hardware:
~4.3x slower than realtime (19.25s to generate a single 11-word French
sentence, 4.48s of audio) — a full tutor page (500-700 words) takes several
minutes. Confirmed slower than Piper (see piper-tts-generate.py's own
docstring: Piper benchmarked 6-20x faster) but kept as primary per explicit
instruction, trading pacing for Qwen's voice quality.
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("QWEN_TTS_PORT", "9243"))
MODEL_PATH = os.environ.get("QWEN_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
DEFAULT_LANG = os.environ.get("QWEN_TTS_LANG", "french")
BIND_HOST = os.environ.get("QWEN_TTS_BIND_HOST", "127.0.0.1")

# use_zero_spk_emb injects NO speaker embedding at all (verified by reading
# qwen3_tts.py: speaker_embed stays None when ref_audio/voice are both
# unset) — voice identity is pure emergent behavior of the codec token
# sampling conditioned only on text, not a single random "draw". Reseeding
# mx.random before each call (tried first) only produces the same voice
# when the text is byte-identical; different text consumes the RNG stream
# differently and the voice drifts within the first couple tokens — this
# is why chunk 2 of a stream sounded like a different speaker even with a
# fixed seed.
#
# Real fix: synthesize one reference clip ONCE at warm-up (zero-shot, deterministic
# via a fixed seed) and reuse it as ref_audio/ref_text for every real request.
# That routes generate() through the ICL/voice-cloning path (extract_speaker_embedding
# on real audio -> a real embedding vector prepended to every generation), which
# pins the voice regardless of what text follows.
QWEN_TTS_SEED = int(os.environ.get("QWEN_TTS_SEED", "777"))
REF_TEXT = "Bonjour, ceci est une voix de référence pour le clonage."

_ref_audio = None

log = logging.getLogger("qwen_tts_daemon")

_model = None
_model_lock = threading.Lock()
# Dry-run tested 2026-09-15 (2 threads, same in-process model.generate() calls
# our daemon makes): no crash, no corrupted output, and real wall-clock benefit
# (concurrent 14.3s vs serial-summed 16.9s for 2 short calls) — Metal doesn't
# fully serialize this. A plain Lock (max 1) was stricter than the evidence
# supports; capped at 2 concurrent rather than unbounded to bound GPU memory
# (~6.5-6.85GB per generation) and because this is untested past 2-wide.
_gen_lock = threading.Semaphore(2)


def _get_model():
    global _model, _ref_audio
    if _model is None:
        with _model_lock:
            if _model is None:
                from mlx_audio.tts.utils import load_model
                import mlx.core as mx

                log.info("loading Qwen3-TTS model (%s)", MODEL_PATH)
                _model = load_model(model_path=MODEL_PATH)
                log.info("Qwen3-TTS loaded, sample_rate=%d", _model.sample_rate)

                log.info("generating reference clip to pin voice identity")
                mx.random.seed(QWEN_TTS_SEED)
                ref_results = list(
                    _model.generate(
                        text=REF_TEXT,
                        lang_code="french",
                        split_pattern=None,
                        use_zero_spk_emb=True,
                        verbose=False,
                    )
                )
                _ref_audio = ref_results[0].audio
                log.info("reference clip ready (%d samples)", _ref_audio.shape[-1])
    return _model


def _to_wav_bytes(audio, sample_rate: int) -> bytes:
    import numpy as np

    samples = np.array(audio)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


_SENTENCE_END = __import__("re").compile(r"(?<=[.!?…])\s+")


def _sentence_chunks(text: str, min_chars: int = 220) -> list[str]:
    """Split into sentence-sized chunks so streaming starts fast and each
    chunk generates in a bounded, predictable time. Glues fragments under
    min_chars onto the next sentence — same fix local-kokoro-server.py uses
    for its English sentence-streaming, so a bare "Oui." isn't its own
    clipped chunk."""
    parts = [p.strip() for p in _SENTENCE_END.split(text.strip()) if p.strip()]
    chunks: list[str] = []
    pending = ""
    for part in parts:
        pending = f"{pending} {part}".strip() if pending else part
        if len(pending) >= min_chars:
            chunks.append(pending)
            pending = ""
    if pending:
        if chunks:
            chunks[-1] = f"{chunks[-1]} {pending}"
        else:
            chunks.append(pending)
    return chunks or [text.strip()]


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.request.settimeout(600)  # generation can take several minutes/page

    def log_message(self, fmt, *args):
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
        lang_code = payload.get("lang_code") or DEFAULT_LANG
        if not text:
            self.send_response(400)
            self.end_headers()
            return
        try:
            model = _get_model()
            joined = "\n".join(_sentence_chunks(text))
            with _gen_lock:
                results = list(
                    model.generate(
                        text=joined,
                        lang_code=lang_code,
                        split_pattern="\n",
                        ref_audio=_ref_audio,
                        ref_text=REF_TEXT,
                        verbose=False,
                    )
                )
            if not results:
                raise RuntimeError("no audio segments generated")
            import numpy as np

            audio = np.concatenate([np.array(r.audio) for r in results])
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

    def _handle_stream(self):
        """Stream one WAV chunk per sentence as it finishes generating,
        4-byte big-endian length prefix per chunk (mirrors kokoro_daemon.py's
        _handle_stream framing) over a connection-close-terminated body —
        so a caller hears the first sentence in seconds instead of waiting
        minutes for the whole page."""
        import struct

        payload = self._read_json_body()
        if payload is None:
            self.send_response(400)
            self.end_headers()
            return
        text = (payload.get("text") or "").strip()
        lang_code = payload.get("lang_code") or DEFAULT_LANG
        if not text:
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

        try:
            model = _get_model()
            chunks = _sentence_chunks(text)
            with _gen_lock:
                for chunk in chunks:
                    for r in model.generate(
                        text=chunk,
                        lang_code=lang_code,
                        split_pattern=None,
                        ref_audio=_ref_audio,
                        ref_text=REF_TEXT,
                        verbose=False,
                    ):
                        wav = _to_wav_bytes(r.audio, model.sample_rate)
                        self.wfile.write(struct.pack(">I", len(wav)))
                        self.wfile.write(wav)
                        self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-stream — nothing to clean up
        except Exception as e:
            log.warning("stream synthesis failed: %s", e)


def main():
    log.info("warming Qwen3-TTS daemon on %s:%d", BIND_HOST, PORT)
    _get_model()  # pay the load cost now, not on the first real request
    server = ThreadingHTTPServer((BIND_HOST, PORT), _Handler)
    log.info("Qwen3-TTS daemon listening on %s:%d", BIND_HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    main()
