"""Audio plumbing for the voice system: decode, encode, capture, playback.

Two audio shapes exist in the pipeline and nothing else:

* **PCM16 mono @ 16 kHz** on the wire (mic frames up, ASR input). The client
  downsamples in an AudioWorklet, so the server never resamples a live stream.
* **WAV (RIFF) bytes** for anything we hand to a player or a browser — Kokoro
  returns WAV per sentence, and browsers want a complete container per clip.

Decoding is ffmpeg (already a hard dependency of the STT path). Playback is
`afplay` on macOS with `ffplay`/`play` fallbacks, and always stoppable: barge-in
means killing the current player, not waiting for it to finish.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
FFMPEG = os.environ.get("CLIXEN_FFMPEG") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

_MIME_SUFFIX = {
    "audio/webm": ".webm",
    "audio/webm;codecs=opus": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def have_ffmpeg() -> bool:
    return bool(FFMPEG) and Path(FFMPEG).exists()


def mime_suffix(mime: str, default: str = ".webm") -> str:
    return _MIME_SUFFIX.get((mime or "").split(";")[0].strip().lower(), default)


# ── conversions ────────────────────────────────────────────────────────────


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    """Little-endian signed 16-bit PCM → float32 in [-1, 1]."""
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def resample(samples: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Linear resample — used for tests and for clips a client sent at 24/48 kHz.

    Live mic audio never comes through here: the app hands us 16 kHz already.
    """
    if src_rate == dst_rate or len(samples) == 0:
        return samples.astype(np.float32)
    n = int(len(samples) * dst_rate / src_rate)
    return np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples).astype(np.float32)


def wav_bytes(samples: np.ndarray, rate: int = 24000) -> bytes:
    """Float samples → a complete 16-bit mono WAV container."""
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(float_to_pcm16(np.asarray(samples)))
    return buf.getvalue()


def wav_to_float(data: bytes) -> tuple[np.ndarray, int]:
    """WAV bytes → (float32 mono, rate). Raises wave.Error on junk."""
    import io

    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise wave.Error("only 16-bit mono WAV is supported")
        return pcm16_to_float(w.readframes(w.getnframes())), w.getframerate()


def decode_to_float(data: bytes, suffix: str = ".webm", rate: int = SAMPLE_RATE) -> np.ndarray:
    """Decode any container ffmpeg understands (webm/opus, mp4, mp3…) to mono float.

    ffmpeg reads from a temp file rather than a pipe: MediaRecorder's webm has no
    duration in its header and pipe input makes some demuxers reject it.
    """
    if not data:
        return np.zeros(0, dtype=np.float32)
    fd, path = tempfile.mkstemp(suffix=suffix or ".bin")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return decode_file_to_float(path, rate=rate)
    finally:
        Path(path).unlink(missing_ok=True)


def decode_file_to_float(path: str, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Decode a file to mono float32 at `rate` via ffmpeg's s16le pipe."""
    res = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(path), "-ar", str(rate), "-ac", "1", "-f", "s16le", "-"],
        capture_output=True,
        timeout=120,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg decode failed: {res.stderr.decode(errors='replace')[:200]}")
    return pcm16_to_float(res.stdout)


# ── playback (stoppable) ───────────────────────────────────────────────────

_player_lock = threading.Lock()
_player: subprocess.Popen | None = None


def _player_cmd(path: str) -> list[str] | None:
    if sys.platform == "darwin" and shutil.which("afplay"):
        return ["afplay", path]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
    if shutil.which("play"):  # sox
        return ["play", "-q", path]
    return None


def output_available() -> bool:
    return _player_cmd("x") is not None


def play_file(path: str, *, blocking: bool = True) -> bool:
    """Play a file through the first available player. Returns False if none."""
    global _player
    cmd = _player_cmd(str(path))
    if cmd is None:
        return False
    stop_playback()  # outside the lock: stop_playback() takes it itself
    with _player_lock:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return False
        _player = proc
    if blocking:
        try:
            proc.wait()
        finally:
            with _player_lock:
                if _player is proc:
                    _player = None
    return True


_last_temp: str | None = None


def play_wav_bytes(wav: bytes, *, blocking: bool = True) -> bool:
    """Play WAV bytes; blocking by default so callers can hold one utterance."""
    global _last_temp
    if not wav:
        return False
    if _last_temp:  # previous non-blocking clip has finished (or been stopped)
        Path(_last_temp).unlink(missing_ok=True)
        _last_temp = None
    fd, path = tempfile.mkstemp(suffix=".wav")
    with os.fdopen(fd, "wb") as f:
        f.write(wav)
    ok = play_file(path, blocking=blocking)
    if blocking or not ok:
        Path(path).unlink(missing_ok=True)
    else:
        _last_temp = path
    return ok


def stop_playback() -> None:
    """Kill the current player — this is what barge-in does to a reply."""
    global _player
    with _player_lock:
        proc, _player = _player, None
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=1.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ── capture (wake listener only; the app owns the mic otherwise) ───────────


def mic_available() -> bool:
    try:
        import sounddevice as sd

        sd.query_devices(kind="input")
        return True
    except Exception:
        return False


def input_device_name() -> str | None:
    try:
        import sounddevice as sd

        return str(sd.query_devices(kind="input").get("name", "")) or None
    except Exception:
        return None


def open_input(*, rate: int = SAMPLE_RATE, frame_samples: int = 480, device=None):
    """A mono 16-bit input stream at `rate`. Caller closes it.

    `frame_samples=480` is 30 ms at 16 kHz — the only frame size webrtcvad
    accepts (10/20/30 ms), so the VAD path never has to re-frame.
    """
    import sounddevice as sd

    return sd.InputStream(
        samplerate=rate,
        channels=1,
        dtype="int16",
        blocksize=frame_samples,
        device=device or (os.environ.get("CLIXEN_MIC_DEVICE") or None),
    )


def frame_chunks(samples: np.ndarray, frame_samples: int):
    """Yield fixed-size float frames, dropping the ragged tail."""
    for i in range(0, len(samples) - frame_samples + 1, frame_samples):
        yield samples[i : i + frame_samples]
