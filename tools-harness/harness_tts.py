"""
TTS helpers extracted from harness.py — self-contained, no dependency on the
rest of the orchestrator's dispatch chain.
"""

import os
import re
import shutil
import sys

_MD_STRIP_RE = re.compile(r"(\*{1,3}|_{1,3}|`{1,3}|~~|#{1,6}\s?)")


def _strip_md(text: str) -> str:
    """Remove markdown tokens so TTS speaks clean plain text."""
    return _MD_STRIP_RE.sub("", text).strip()


def _kokoro_paths() -> tuple[str, str]:
    """KOKORO_ONNX_PATH / KOKORO_VOICES_PATH with repo-relative defaults
    (same pattern as chat_ui.py) — never KeyError for a fresh user."""
    from pathlib import Path

    repo_models = Path(__file__).resolve().parent.parent.parent / "models"
    onnx = os.environ.get("KOKORO_ONNX_PATH", str(repo_models / "kokoro-v1.0.onnx"))
    voices = os.environ.get("KOKORO_VOICES_PATH", str(repo_models / "voices-v1.0.bin"))
    return onnx, voices


def _play_wav(path: str) -> None:
    """Play a wav file — afplay on macOS, ffplay fallback elsewhere."""
    import subprocess

    if sys.platform == "darwin" and shutil.which("afplay"):
        subprocess.run(["afplay", path])
    elif shutil.which("ffplay"):
        subprocess.run(["ffplay", "-nodisp", "-autoexit", path], capture_output=True)
    elif shutil.which("play"):  # sox
        subprocess.run(["play", path], capture_output=True)
    else:
        raise RuntimeError("no audio player found (afplay/ffplay/play) — install one")


def _speak(text: str, voice: str = "af_heart"):
    import soundfile as sf
    import tempfile
    from kokoro_onnx import Kokoro

    onnx_path, voices_path = _kokoro_paths()
    kokoro = Kokoro(onnx_path, voices_path)
    samples, sr = kokoro.create(_strip_md(text), voice=voice, speed=1.0, lang="en-us")

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        sf.write(tmp.name, samples, sr)
        _play_wav(tmp.name)
    finally:
        os.unlink(tmp.name)
