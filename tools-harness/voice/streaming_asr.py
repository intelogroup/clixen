"""Streaming ASR on Parakeet-TDT (MLX) — the voice agent's ears.

Why not whisper: whisper-cli / faster-whisper are *offline* decoders. The only
way to get partial text out of them is to re-decode a rolling window, which
rewrites everything you already showed, burns CPU proportional to the window
and gives no stable prefix. A conversation needs the opposite: text that only
grows, ~300-500 ms behind the voice.

`parakeet_mlx` ships exactly that: `BaseParakeet.transcribe_stream()` returns a
`StreamingParakeet` that keeps each encoder layer's KV cache
(`RotatingConformerCache`), switches the encoder to local attention, and splits
decoding into a **finalized** region (tokens that can never change) and a
**draft** region (re-decoded every chunk, may be rewritten). That is the same
shape as the streaming conformer ASRs hosted voice stacks use, running locally.

Measured on this M4, 10 s clip, 0.5 s hop, `parakeet-tdt-0.6b-v3`:
    add_audio 406 ms mean / 535 ms p95 / 655 ms first chunk
    offline whole-clip decode: 501 ms (RTF 0.05) — kept for file transcription
    model load 1.4 s, warm-up 1 s of silence

Threading: MLX streams belong to the thread that created them, and
`transcribe_stream().__enter__()` mutates the *shared* encoder's attention
model. So there is exactly one owner thread (`_WORKER`) and one live stream —
i.e. one global ASR instance, which is right for one mic / one user. Callers on
other threads go through `push()`/`submit_push()`, never touch MLX directly.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

SAMPLE_RATE = 16000
DEFAULT_MODEL = os.environ.get("CLIXEN_ASR_MODEL", "mlx-community/parakeet-tdt-0.6b-v3")

# 0.5 s of audio per add_audio() call: below this the fixed per-call cost
# dominates, above it the caption lags the voice. See the latency table above.
DEFAULT_HOP_S = 0.5
MAX_UTTERANCE_S = 25.0  # past the rotating-cache window the draft stops paying its way

_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parakeet")
_MODEL = None            # shared on _WORKER only
_MODEL_ID = ""
_INSTANCE: "StreamingParakeetASR | None" = None
_INSTANCE_LOCK = threading.Lock()


@dataclass
class StreamUpdate:
    """What one audio chunk produced.

    `stable` is the finalized prefix: append-only, safe to diff against the
    previous caption. `text` includes the draft tail, which the next chunk may
    rewrite — render it, but never treat it as final.
    """

    text: str
    stable: str
    audio_s: float
    latency_ms: float
    overflow: bool = False


def available() -> bool:
    """True when the MLX Parakeet stack can actually run here."""
    try:
        import mlx.core  # noqa: F401
        import parakeet_mlx  # noqa: F401
    except Exception:
        return False
    return True


def model_cached(model_id: str = DEFAULT_MODEL) -> bool:
    """True if the weights are already on disk (no network needed)."""
    try:
        from huggingface_hub import scan_cache_dir
    except Exception:
        return False
    try:
        for repo in scan_cache_dir().repos:
            if repo.repo_id.lower() == model_id.lower():
                return True
    except Exception:
        return False
    return False


class StreamingParakeetASR:
    """One live utterance at a time, over a persistent KV-cached decoder."""

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        *,
        context_size: tuple[int, int] = (256, 256),
        depth: int = 1,
    ) -> None:
        self.model_id = model_id
        self.context_size = context_size
        self.depth = depth
        self._stream = None
        self._entered = False
        self._fed_s = 0.0
        self._finalized = ""
        self._closed = False

    # ── worker-thread internals ────────────────────────────────────────────

    def _ensure_stream(self) -> None:
        """Load the model + open the streaming context. Runs on _WORKER."""
        if self._entered:
            return
        global _MODEL, _MODEL_ID
        import mlx.core as mx
        from parakeet_mlx import from_pretrained
        from parakeet_mlx.audio import get_logmel

        if _MODEL is None or _MODEL_ID != self.model_id:
            model = from_pretrained(self.model_id)
            # Weights load lazily; one pass on 1 s of silence pulls them in now
            # so the first real chunk doesn't pay for it.
            model.generate(get_logmel(mx.zeros(SAMPLE_RATE), model.preprocessor_config))
            _MODEL, _MODEL_ID = model, self.model_id

        self._stream = _MODEL.transcribe_stream(context_size=self.context_size, depth=self.depth)
        self._stream.__enter__()
        self._entered = True

    def _reset_buffers(self) -> None:
        """Start a new utterance on the same (already warm) streaming context.

        Cheaper and safer than leaving/re-entering the context: re-entering
        flips the shared encoder's attention model and calls mx.clear_cache(),
        which costs a few hundred ms on the next chunk.
        """
        import mlx.core as mx

        s = self._stream
        s.audio_buffer = mx.array([])
        s.mel_buffer = None
        s.decoder_hidden = None
        s.last_token = None
        s.finalized_tokens = []
        s.draft_tokens = []
        for cache in s.cache:
            cache.keys = None
            cache.values = None
            cache.conv = None
            cache.offset = 0
        self._fed_s = 0.0
        self._finalized = ""

    @staticmethod
    def _stable_text(stream) -> str:
        from parakeet_mlx.alignment import tokens_to_sentences

        if not stream.finalized_tokens:
            return ""
        sentences = tokens_to_sentences(stream.finalized_tokens, stream.decoding_config.sentence)
        return "".join(s.text for s in sentences).strip()

    def _push_array(self, pcm, *, force: bool = False) -> StreamUpdate:
        import mlx.core as mx

        t0 = time.perf_counter()
        self._ensure_stream()
        if self._fed_s == 0.0:
            self._reset_buffers()

        overflow = (not force) and self._fed_s >= MAX_UTTERANCE_S
        if not overflow:
            self._stream.add_audio(mx.array(pcm))
            self._fed_s += len(pcm) / SAMPLE_RATE
            self._finalized = self._stable_text(self._stream)

        return StreamUpdate(
            text=self._stream.result.text.strip(),
            stable=self._finalized,
            audio_s=round(self._fed_s, 2),
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            overflow=overflow,
        )

    # ── public API (any thread) ────────────────────────────────────────────

    def submit_push(self, pcm, *, force: bool = False) -> Future:
        return _WORKER.submit(self._push_array, pcm, force=force)

    def push(self, pcm, *, force: bool = False) -> StreamUpdate:
        """Feed mono float32 audio at 16 kHz; block for the new caption."""
        return self.submit_push(pcm, force=force).result()

    def start_utterance(self) -> Future:
        """Reset for a new utterance (called when VAD opens)."""
        return _WORKER.submit(self._start)

    def _start(self) -> None:
        self._ensure_stream()
        self._reset_buffers()

    @property
    def text(self) -> str:
        return self._stream.result.text.strip() if self._entered else ""

    @property
    def stable_text(self) -> str:
        return self._finalized

    @property
    def audio_s(self) -> float:
        return round(self._fed_s, 2)

    def finalize(self) -> str:
        """Flush the draft region with ~0.4 s of silence and return the text."""
        import numpy as np

        tail = np.zeros(int(0.4 * SAMPLE_RATE), dtype=np.float32)
        try:
            return _WORKER.submit(self._push_array, tail, force=True).result().text
        except Exception:  # pragma: no cover - model failure shouldn't kill the turn
            return self.text

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        def _exit() -> None:
            if self._entered and self._stream is not None:
                try:
                    self._stream.__exit__(None, None, None)
                except Exception:
                    pass
            self._entered = False
            self._stream = None

        try:
            _WORKER.submit(_exit).result(timeout=10)
        except Exception:
            pass


def get_asr(**kwargs) -> StreamingParakeetASR:
    """Process-wide ASR (one mic, one live stream — see the module docstring)."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = StreamingParakeetASR(**kwargs)
        return _INSTANCE


def stream_active() -> bool:
    """True while a streaming context has the encoder in local-attention mode.

    An offline `generate()` on the same model would inherit that attention, so
    callers doing file transcription must check this and pick another engine.
    """
    inst = _INSTANCE
    return bool(inst is not None and inst._entered)


def transcribe_array(samples, model_id: str = DEFAULT_MODEL) -> str:
    """Offline decode of a 16 kHz mono float array (same model, no streaming)."""
    import numpy as np

    def _run() -> str:
        import mlx.core as mx
        from parakeet_mlx import from_pretrained
        from parakeet_mlx.audio import get_logmel

        global _MODEL, _MODEL_ID
        if _MODEL is None or _MODEL_ID != model_id:
            model = from_pretrained(model_id)
            model.generate(get_logmel(mx.zeros(SAMPLE_RATE), model.preprocessor_config))
            _MODEL, _MODEL_ID = model, model_id
        mel = get_logmel(mx.array(np.asarray(samples, dtype=np.float32)), _MODEL.preprocessor_config)
        return _MODEL.generate(mel)[0].text.strip()

    return _WORKER.submit(_run).result()
