"""TTS + the text preparation that speech needs.

Synthesis lives in the warm Kokoro daemon (:9237, started by core.py) because
loading the 325 MB ONNX model into the API process costs ~1.8 s and pins memory.
The in-process path stays as a fallback for processes that have no daemon
(clixen_ptt, a CLI), and macOS `say` is the last resort so a reply is never
silently dropped.

Two text rules matter and both were learned the hard way:

* **Tool-progress labels must never be spoken.** `cloud_client` injects
  "🔍 Searching the web…" into the token stream for the UI's live bubble; TTS
  would read it out mid-answer.
* **Kokoro dies past ~510 phonemes** (IndexError, not a clean truncation), so
  every chunk is hard-wrapped to 300 characters before it is synthesized.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

DEFAULT_VOICE = os.environ.get("CLIXEN_TTS_VOICE", "af_heart")
KOKORO_URL = os.environ.get("KOKORO_URL", "http://127.0.0.1:9237").rstrip("/")
KOKORO_MAX_CHARS = 300

_MD_STRIP_RE = re.compile(r"(\*{1,3}|_{1,3}|`{1,3}|~~|#{1,6}\s?)")
_TTS_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\ufe0f\u200d"
    "]+"
)
_TOOL_LABEL_RE = None
_TOOL_LABEL_LOCK = threading.Lock()


def _tool_label_re():
    """Regex for the exact known progress labels, built once, imported lazily.

    Only exact labels are matched: matching on the emoji alone would eat real
    content ("☁️ Condition:" in a weather reply).
    """
    global _TOOL_LABEL_RE
    if _TOOL_LABEL_RE is not None:
        return _TOOL_LABEL_RE
    with _TOOL_LABEL_LOCK:
        if _TOOL_LABEL_RE is None:
            try:
                from clients.cloud_client import _TOOL_PROGRESS_LABELS

                alt = "|".join(re.escape(label) for label in _TOOL_PROGRESS_LABELS.values())
                _TOOL_LABEL_RE = re.compile(rf"\n*(?:{alt})") if alt else False
            except Exception:  # cloud_client unavailable (lightweight caller)
                _TOOL_LABEL_RE = False
    return _TOOL_LABEL_RE


def strip_for_speech(text: str) -> str:
    """Plain text a TTS engine can read: no labels, markdown or emoji."""
    clean = text or ""
    label_re = _tool_label_re()
    if label_re:
        clean = label_re.sub("", clean)
    clean = _MD_STRIP_RE.sub("", clean)
    return _EMOJI_RE.sub("", clean).strip()


def split_sentences(text: str) -> list[str]:
    clean = strip_for_speech(text)
    if not clean:
        return []
    return [part.strip() for part in _TTS_SPLIT_RE.split(clean) if part.strip()] or [clean]


def wrap_for_kokoro(text: str, max_chars: int = KOKORO_MAX_CHARS) -> list[str]:
    """Hard word-wrap so no single synthesis call can exceed Kokoro's limit."""
    words = (text or "").split()
    pieces: list[str] = []
    cur = ""
    for word in words:
        if cur and len(cur) + 1 + len(word) > max_chars:
            pieces.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        pieces.append(cur)
    return pieces


def sentence_boundary(buf: str) -> tuple[str, str]:
    """Split a streaming buffer at the first speakable sentence boundary.

    Returns (ready_sentence, remainder); ("", buf) means "keep accumulating".
    Requires ≥3 words for a newline break and ≥4 for a punctuation break, so a
    short "OK." fragment doesn't get synthesised on its own.
    """
    if not buf:
        return "", ""
    for marker in (".\n", "!\n", "?\n", "\n\n"):
        idx = buf.find(marker)
        if idx != -1:
            chunk = buf[: idx + len(marker)].strip()
            rest = buf[idx + len(marker) :]
            if len(chunk.split()) >= 3:
                return chunk, rest
    m = re.search(r"([.!?…])\s+(?=[A-Z])", buf)
    if m and len(buf[: m.end()].split()) >= 4:
        return buf[: m.end()].strip(), buf[m.end() :]
    return "", buf


# ── synthesis ──────────────────────────────────────────────────────────────


def _auth_headers() -> dict:
    token = os.environ.get("KOKORO_AUTH_TOKEN", "").strip()
    return {"X-Auth-Token": token} if token else {}


def daemon_available(timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(f"{KOKORO_URL}/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _daemon_synthesize(text: str, voice: str) -> bytes:
    import json

    body = json.dumps({"text": text, "voice": voice}).encode()
    req = urllib.request.Request(
        f"{KOKORO_URL}/synthesize",
        data=body,
        headers={"Content-Type": "application/json", **_auth_headers()},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read() if resp.status == 200 else b""


@lru_cache(maxsize=1)
def _local_kokoro():
    """Load Kokoro in-process (fallback only — ~1.8 s and ~325 MB)."""
    from tools.misaki_tokenizer import load_kokoro

    repo_models = Path(__file__).resolve().parents[2] / "models"
    onnx = os.environ.get("KOKORO_ONNX_PATH", str(repo_models / "kokoro-v1.0.onnx"))
    voices = os.environ.get("KOKORO_VOICES_PATH", str(repo_models / "voices-v1.0.bin"))
    try:
        return load_kokoro(onnx, voices)
    except Exception:
        # CoreML provider can be missing on some installs; CPU always works.
        os.environ["ONNX_PROVIDER"] = "CPUExecutionProvider"
        try:
            return load_kokoro(onnx, voices)
        finally:
            os.environ.pop("ONNX_PROVIDER", None)


def _local_synthesize(text: str, voice: str) -> bytes:
    """In-process Kokoro, honouring the same 300-char wrap as the daemon."""
    import numpy as np

    from voice.audio import wav_bytes

    kokoro = _local_kokoro()
    chunks, rate = [], None
    for piece in wrap_for_kokoro(text):
        samples, sr = kokoro.create(piece, voice=voice, speed=1.0, lang="en-us")
        rate = sr
        chunks.append(np.asarray(samples, dtype=np.float32))
    if not chunks or rate is None:
        return b""
    return wav_bytes(np.concatenate(chunks), rate)


def synthesize_wav(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """WAV bytes for `text`: daemon → in-process Kokoro → b"" (never raises).

    Returning empty bytes (rather than raising) is the contract every caller
    depends on: a TTS outage degrades to text, it does not fail the turn.
    """
    clean = strip_for_speech(text)
    if not clean:
        return b""

    pieces = wrap_for_kokoro(clean)
    if len(pieces) == 1:
        try:
            wav = _daemon_synthesize(clean, voice)
            if wav:
                return wav
        except Exception:
            pass  # daemon down → local fallback below
    else:
        try:
            joined = _join_wavs(pieces, voice)
            if joined:
                return joined
        except Exception:
            pass

    try:
        return _local_synthesize(clean, voice)
    except Exception:
        return b""


def _join_wavs(pieces: list[str], voice: str) -> bytes:
    """Concatenate the PCM of several pieces into one WAV."""
    import numpy as np

    from voice.audio import wav_bytes, wav_to_float

    chunks, rate = [], None
    for piece in pieces:
        samples, sr = wav_to_float(_daemon_synthesize(piece, voice))
        rate = sr
        chunks.append(samples)
    if not chunks or rate is None:
        return b""
    return wav_bytes(np.concatenate(chunks), rate)


@lru_cache(maxsize=256)
def synthesize_cached(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    return synthesize_wav(text, voice)


def iter_wavs(text: str, voice: str = DEFAULT_VOICE):
    """Yield one WAV per sentence — the streaming path the session uses."""
    for sentence in split_sentences(text):
        wav = synthesize_cached(sentence, voice)
        if wav:
            yield wav


def speak(text: str, voice: str = DEFAULT_VOICE) -> bool:
    """Synthesize and play, blocking. False when there is nothing/no player."""
    from voice import audio

    wav = synthesize_wav(text, voice)
    if not wav:
        return False
    played = audio.play_wav_bytes(wav, blocking=True)
    if not played and sys.platform == "darwin":
        import subprocess

        try:  # last resort: macOS `say` (no Kokoro, no player binary)
            subprocess.run(["say", "-r", "180", strip_for_speech(text)], timeout=30)
            return True
        except Exception:
            return False
    return played


def health() -> dict:
    return {
        "engine": "kokoro",
        "daemon": daemon_available(),
        "url": KOKORO_URL,
        "voice": DEFAULT_VOICE,
        "max_chars": KOKORO_MAX_CHARS,
    }
