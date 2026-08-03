"""
TTS helpers extracted from harness.py — self-contained, no dependency on the
rest of the orchestrator's dispatch chain.
"""

import os
import re

_MD_STRIP_RE = re.compile(r"(\*{1,3}|_{1,3}|`{1,3}|~~|#{1,6}\s?)")


def _strip_md(text: str) -> str:
    """Remove markdown tokens so TTS speaks clean plain text."""
    return _MD_STRIP_RE.sub("", text).strip()


def _speak(text: str, voice: str = "af_heart"):
    import soundfile as sf
    import subprocess
    import tempfile
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(os.environ["KOKORO_ONNX_PATH"], os.environ["KOKORO_VOICES_PATH"])
    samples, sr = kokoro.create(_strip_md(text), voice=voice, speed=1.0, lang="en-us")

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        sf.write(tmp.name, samples, sr)
        subprocess.run(["afplay", tmp.name])
    finally:
        os.unlink(tmp.name)
