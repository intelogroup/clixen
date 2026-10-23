"""Pluggable Speech-to-Text backend.

Design: the orb (and all voice entry points) should not hard-code one STT
engine. This module defines a clean interface and ships three implementations:

- WhisperSTT (default) — whisper-cpp via whisper-cli (current behaviour, local)
- ParakeetSTT — NVIDIA/Sarvam AI Parakeet-TDT, higher accuracy, multilingual
- Phonón2STT — Gradium/Apple Silicon on-device STT, lowest latency

Switch by setting STT_BACKEND in .env or env_boot. Load order is lazy so a
missing backend never halts core boot.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("stt")

# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------

class STTBackend:
    """Minimal STT interface. Transcription is synchronous (file → transcript)."""

    name: str = "base"

    def transcribe(self, audio_path: str, language: str = "") -> str:
        """Return transcript text, or a diagnostic string starting with ``err:``."""
        raise NotImplementedError

    def supported_models(self) -> list[str]:
        return []

    def health(self) -> dict:
        return {"backend": self.name, "ok": True}


# ---------------------------------------------------------------------------
# 1. Current default — whisper-cpp
# ---------------------------------------------------------------------------

class WhisperSTT(STTBackend):
    name = "whisper"

    def __init__(self) -> None:
        self.whisper_cli = "/opt/homebrew/bin/whisper-cli"
        self.ffmpeg = "/opt/homebrew/bin/ffmpeg"
        self.model_path = str(
            Path(__file__).resolve().parent.parent.parent / "models" / "ggml-base.bin"
        )

    def transcribe(self, audio_path: str, language: str = "") -> str:
        wav_path: str | None = None
        try:
            # whisper.cpp only decodes wav/flac/mp3/ogg, and the orb records webm
            # (opus). Convert first and transcribe the wav: the earlier version
            # converted, deleted the wav, then handed whisper the raw webm, which
            # produced no output at all on every real orb recording.
            wav_path = self._to_wav(audio_path)
            # -nt keeps "[00:00:00.000 --> 00:00:01.800]" out of the transcript
            # that becomes the caption and the model's prompt.
            cmd = [self.whisper_cli, "-m", self.model_path, "-f", wav_path,
                   "--output-txt", "--no-prints", "-nt"]
            if language:
                cmd += ["-l", language]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            txt_path = Path(wav_path + ".txt")  # whisper writes <input>.txt
            if txt_path.exists():
                return txt_path.read_text().strip()
            if res.stdout.strip():
                return res.stdout.strip()
            return f"err: whisper produced no output ({res.stderr[:200]})"
        except subprocess.TimeoutExpired:
            return "err: transcription timed out (>120s)"
        except Exception as exc:  # pragma: no cover
            return f"err: {exc!r}"
        finally:
            if wav_path:
                Path(wav_path).unlink(missing_ok=True)
                Path(wav_path + ".txt").unlink(missing_ok=True)

    def _to_wav(self, input_path: str) -> str:
        """Decode `input_path` to a 16 kHz mono WAV in /tmp and return its path.

        The caller owns the file and unlinks it.
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir="/tmp") as f:
            wav_path = f.name
        try:
            subprocess.run(
                [self.ffmpeg, "-y", "-i", input_path, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path],
                check=True, capture_output=True, timeout=120,
            )
        except Exception:
            Path(wav_path).unlink(missing_ok=True)
            raise
        return wav_path

    def supported_models(self) -> list[str]:
        return ["ggml-base.bin"]


# ---------------------------------------------------------------------------
# 2. Parakeet — NVIDIA/Sarvam AI open ASR, higher accuracy, multilingual
# ---------------------------------------------------------------------------

class ParakeetSTT(STTBackend):
    name = "parakeet"

    def __init__(self) -> None:
        # Parakeet is used via the HuggingFace `transformers` pipeline. We
        # keep this as a *reference* implementation — swap in your preferred
        # ONNX/Sherpa binding in production. This class is here to document
        # the interface; it raises NotImplementedError until wired up.
        raise NotImplementedError("ParakeetSTT not yet implemented")

    def supported_models(self) -> list[str]:
        return ["parakeet-tdt-0.6b-v2", "parakeet-tdt-0.6b-v3"]


# ---------------------------------------------------------------------------
# 3. Phonón-2 — Apple Silicon on-device STT (Gradium)
# ---------------------------------------------------------------------------

class Phonón2STT(STTBackend):
    name = "phonon2"

    def __init__(self) -> None:
        # Phonon-2 is a mobile-first STT (Apple Neural Engine / CoreML).
        # This is a reference stub — replace with the actual binding in your
        # deployment path.
        raise NotImplementedError("Phónón-2 STT not yet implemented")

    def supported_models(self) -> list[str]:
        return ["phonon-2"]

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_LAST_STT: STTBackend | None = None


def get_stt() -> STTBackend:
    """Return the active STT backend. Lazy-init so a missing plugin never blocks core boot.

    Uses the backend name from STT_BACKEND env var (default: whisper). Caches the
    result so repeated calls don't re-instantiate heavy objects (e.g. whisper model).
    """
    global _LAST_STT
    if _LAST_STT is not None:
        return _LAST_STT
    backend_name = os.environ.get("STT_BACKEND", "whisper").strip().lower()
    _LAST_STT = set_stt_backend(backend_name)
    return _LAST_STT


def set_stt_backend(backend_name: str = "whisper") -> STTBackend:
    """Factory: create and cache the requested backend.

    If a backend class raises NotImplementedError at construction (e.g. ParakeetSTT
    is a stub not yet wired up), we gracefully fall back to WhisperSTT so the app
    always has a working default.
    """
    global _LAST_STT
    backends = {"parakeet": ParakeetSTT, "phonon2": Phonón2STT}
    cls = backends.get(backend_name, WhisperSTT)
    try:
        _LAST_STT = cls()
    except Exception as exc:
        log.warning("STT backend %s unavailable (%s); falling back to WhisperSTT", backend_name, exc)
        _LAST_STT = WhisperSTT()
    return _LAST_STT
