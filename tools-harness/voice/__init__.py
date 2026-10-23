"""Clixen voice system — one pipeline for every voice surface.

Modules (added incrementally, see docs/agents/voice.md):

    streaming_asr   Parakeet-TDT on MLX with the encoder KV cache kept across
                    chunks — incremental, stable-prefix transcripts. This is the
                    voice agent's ears; whisper is now file-transcription only.
    audio           ffmpeg decode / WAV helpers / mic + playback.
    tts             Kokoro (warm daemon :9237) with in-process and `say` fallbacks.
    agent           VoiceAgent: turn lifecycle, barge-in, event stream.
    session         Duplex session for the desktop app (WS, continuous).

Nothing here imports `harness` at module level: the brain is injected by the
caller so `voice` stays importable from lightweight processes (clixen_ptt,
the wake listener) without dragging in the orchestrator.
"""
from __future__ import annotations

__all__ = ["SAMPLE_RATE", "available", "get_asr", "StreamingParakeetASR", "StreamUpdate"]

SAMPLE_RATE = 16000


def __getattr__(name):  # lazy so `import voice` stays cheap on non-Apple hardware
    if name in {"available", "get_asr", "StreamingParakeetASR", "StreamUpdate"}:
        from voice import streaming_asr

        return getattr(streaming_asr, name)
    raise AttributeError(name)
