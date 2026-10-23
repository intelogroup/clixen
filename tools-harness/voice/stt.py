"""One STT façade: Parakeet (MLX) first, whisper-cpp as the fallback.

Parakeet-TDT 0.6b is more accurate *and* faster than whisper-base on this
hardware (0.50 s vs 0.66-0.80 s for the same 10 s clip) and it is the only one
of the two that can also stream (`voice/streaming_asr.py`). whisper.cpp stays
because it is a self-contained binary: it still works when MLX/ANE is
unavailable, and it is used for file transcription while a live stream owns the
Parakeet encoder (see `streaming_asr.stream_active()`).

Contract: failures return an `"err: ..."` string and never raise — every caller
(audio upload endpoint, wake listener, agent) treats that as "no transcript"
and answers with an `error` event instead of sending gibberish to the model.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from voice import audio, streaming_asr

SAMPLE_RATE = 16000
WHISPER_CLI = os.environ.get("CLIXEN_WHISPER_CLI") or "/opt/homebrew/bin/whisper-cli"
WHISPER_MODEL = os.environ.get("CLIXEN_WHISPER_MODEL") or str(
    Path(__file__).resolve().parents[2] / "models" / "ggml-base.bin"
)

_ERR_PREFIXES = ("err:", "File not found", "ffmpeg", "Whisper", "Transcription")


def is_error(text: str) -> bool:
    """True when `transcribe_*` gave up rather than produced words."""
    return not text or text.startswith(_ERR_PREFIXES)


def engine() -> str:
    """Which engine `transcribe_*` would use right now."""
    if streaming_asr.available() and not streaming_asr.stream_active():
        return "parakeet"
    return "whisper-cpp"


def health() -> dict:
    return {
        "engine": engine(),
        "parakeet_available": streaming_asr.available(),
        "parakeet_model": streaming_asr.DEFAULT_MODEL,
        "whisper_cli": Path(WHISPER_CLI).exists(),
        "whisper_model": Path(WHISPER_MODEL).exists(),
    }


def transcribe_path(path: str, language: str = "") -> str:
    """Transcribe a file on disk. Returns words or an `err:` string."""
    path = str(path)
    if not Path(path).exists():
        return f"err: File not found: {path}"
    if engine() == "parakeet":
        try:
            return streaming_asr.transcribe_array(audio.decode_file_to_float(path))
        except Exception as exc:  # MLX/ANE hiccup → whisper still works
            pass  # fall through
    return _whisper(path, language)


def transcribe_bytes(data: bytes, suffix: str = ".webm", language: str = "") -> str:
    """Transcribe an upload/blob (webm/opus from a recorder, wav, mp3…)."""
    if not data:
        return "err: empty audio"
    if engine() == "parakeet":
        try:
            return streaming_asr.transcribe_array(audio.decode_to_float(data, suffix))
        except Exception:
            pass
    fd, path = tempfile.mkstemp(suffix=suffix or ".bin")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return _whisper(path, language)
    finally:
        Path(path).unlink(missing_ok=True)


def _whisper(path: str, language: str = "") -> str:
    """whisper.cpp on a 16 kHz mono WAV (it cannot decode webm/mp4 itself)."""
    wav_path = None
    try:
        wav_path = _to_wav(path)
        cmd = [WHISPER_CLI, "-m", WHISPER_MODEL, "-f", wav_path, "--output-txt", "--no-prints", "-nt"]
        if language:
            cmd += ["-l", language]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        txt = Path(wav_path + ".txt")  # whisper writes <input>.txt
        if txt.exists():
            return txt.read_text().strip()
        if res.stdout.strip():
            return res.stdout.strip()
        return f"err: whisper produced no output ({res.stderr[:200]})"
    except subprocess.TimeoutExpired:
        return "err: transcription timed out (>120s)"
    except Exception as exc:
        return f"err: {exc!r}"
    finally:
        if wav_path:
            Path(wav_path).unlink(missing_ok=True)
            Path(wav_path + ".txt").unlink(missing_ok=True)


def _to_wav(input_path: str) -> str:
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        subprocess.run(
            [audio.FFMPEG, "-y", "-v", "error", "-i", input_path, "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", wav_path],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except Exception:
        Path(wav_path).unlink(missing_ok=True)
        raise
    return wav_path
