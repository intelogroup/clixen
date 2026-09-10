"""
Speaker verification using Resemblyzer — a tiny (1.6MB) model trained specifically
for voice biometrics. Unlike whisper's encoder (which captures phonetic content),
Resemblyzer embeddings are optimized for speaker identity discrimination.

Enrollment:  user speaks a calibration phrase → 256-dim speaker embedding
Verification: audio comes in → embedding → cosine similarity vs voiceprint

Music/singing: Resemblyzer extracts vocal timbre features (formants, pitch, rhythm).
A different speaker's voice (including a singer) produces a meaningfully different
embedding, even if the lyrics or melody are identical to the enrolled user's speech.

Usage:
    python -m tools.voiceprint enroll /path/to/audio.wav
    python -m tools.voiceprint verify /path/to/audio.wav /path/to/voiceprint.npy
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

VOICEPRINT_DIR = Path.home() / ".config" / "g4l" / "voiceprint"
EMBEDDING_DIM = 256  # Resemblyzer embedding size
DEFAULT_THRESHOLD = 0.55  # cosine similarity — speaker match threshold (lowered for live mic variance)
ENROLL_DURATION_S = 5  # how much speech to capture for enrollment

_encoder = None  # lazy singleton


def _ensure_pkg_resources_shim() -> None:
    """webrtcvad (resemblyzer's VAD dependency) does `import pkg_resources`
    purely to read its own version via pkg_resources.get_distribution(...).
    pkg_resources was removed from setuptools 81+ — this repo pinned
    setuptools<81 just to keep that one deprecated import alive, which kept
    a moderate-severity Dependabot alert (sdist exclusion bypass) open with
    no real upgrade path. Stand in a tiny shim backed by importlib.metadata
    (stdlib) instead, so setuptools can go to latest. No-op if the real
    pkg_resources is still installed.
    """
    import sys
    if sys.modules.get("pkg_resources") is not None:
        return
    try:
        import pkg_resources  # noqa: F401
        return
    except ImportError:
        pass
    import importlib.metadata as _im
    import types
    shim = types.ModuleType("pkg_resources")

    def get_distribution(name):
        class _Dist:
            version = _im.version(name)
        return _Dist()

    shim.get_distribution = get_distribution
    sys.modules["pkg_resources"] = shim


def _get_encoder():
    global _encoder
    if _encoder is None:
        _ensure_pkg_resources_shim()
        from resemblyzer import VoiceEncoder
        _encoder = VoiceEncoder()
    return _encoder


def _extract_embedding(audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """
    Extract speaker embedding from raw audio.
    Returns L2-normalized 256-dim vector.
    """
    if audio.ndim > 1:
        audio = audio.mean(axis=1)  # mono
    if len(audio) < sample_rate * 0.5:
        raise ValueError(f"Audio too short: {len(audio) / sample_rate:.1f}s (need ≥0.5s)")

    encoder = _get_encoder()
    embedding = encoder.embed_utterance(audio.astype(np.float64))
    return embedding.astype(np.float32)


def enroll(audio_path: str) -> np.ndarray:
    """Create a voiceprint from calibration audio."""
    audio = _read_audio(audio_path)
    return _extract_embedding(audio, 16000)


def verify_from_path(
    audio_path: str,
    voiceprint_path: str,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[bool, float]:
    """Check if audio file matches the enrolled voiceprint."""
    voiceprint = np.load(voiceprint_path)
    audio = _read_audio(audio_path)
    embedding = _extract_embedding(audio, 16000)
    similarity = float(np.dot(voiceprint, embedding))
    return (similarity >= threshold, similarity)


verify = verify_from_path  # backward compatibility


def _read_audio(path: str) -> np.ndarray:
    """Read audio file to mono float32 16kHz numpy array."""
    path = os.path.expanduser(path)
    suffix = Path(path).suffix.lower()

    try:
        import soundfile as sf
        audio, sr = sf.read(path)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != 16000:
            from scipy import signal
            ratio = 16000 / sr
            audio = signal.resample(audio, int(len(audio) * ratio))
        return audio.astype(np.float32)
    except Exception:
        pass

    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_file(path).set_frame_rate(16000).set_channels(1)
        samples = np.array(seg.get_array_of_samples()).astype(np.float32) / 32768.0
        return samples
    except Exception:
        pass

    raise RuntimeError(
        "Need soundfile (pip install soundfile) or pydub (pip install pydub) to read audio."
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Speaker voiceprint — enroll and verify")
    sub = parser.add_subparsers(dest="cmd", required=True)

    enroll_p = sub.add_parser("enroll")
    enroll_p.add_argument("audio", help="Path to calibration WAV (3-5s of you speaking)")
    enroll_p.add_argument("--out", help="Output path for voiceprint (default: ~/.config/g4l/voiceprint/)")

    verify_p = sub.add_parser("verify")
    verify_p.add_argument("audio", help="Audio file to check")
    verify_p.add_argument("voiceprint", help="Path to saved voiceprint .npy")
    verify_p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)

    args = parser.parse_args()

    if args.cmd == "enroll":
        print(f"[voiceprint] extracting from {args.audio}...")
        t0 = time.time()
        emb = enroll(args.audio)
        elapsed = time.time() - t0
        out_path = args.out or str(VOICEPRINT_DIR / "voiceprint.npy")
        os.makedirs(os.path.dirname(out_path) or VOICEPRINT_DIR, exist_ok=True)
        np.save(out_path, emb)
        print(f"[voiceprint] saved to {out_path} ({EMBEDDING_DIM}-dim Resemblyzer, {elapsed:.1f}s)")

    elif args.cmd == "verify":
        print(f"[voiceprint] verifying {args.audio} against {args.voiceprint}...")
        t0 = time.time()
        match, sim = verify(args.audio, args.voiceprint, args.threshold)
        elapsed = time.time() - t0
        print(f"[voiceprint] match={match} similarity={sim:.4f} threshold={args.threshold} ({elapsed:.1f}s)")
        if not match:
            sys.exit(1)


if __name__ == "__main__":
    main()
