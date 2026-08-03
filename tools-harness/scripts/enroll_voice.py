#!/usr/bin/env python3
"""
Record your voice, enroll a voiceprint, then verify.
Usage:
    python scripts/enroll_voice.py              # interactive enrollment
    python scripts/enroll_voice.py --verify     # verify against existing voiceprint
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RECORD_SECS = 5
OUT_PATH = str(Path.home() / ".config" / "g4l" / "voiceprint" / "voiceprint.npy")


def _record(duration_s: int, label: str) -> str:
    """Record audio from default mic. Returns path to temp WAV."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="g4l_voice_")
    os.close(fd)
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"  Recording {duration_s}s... SPEAK NOW")
    print(f"{'='*50}")
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "avfoundation",
            "-i", ":0",  # default mic
            "-t", str(duration_s),
            "-ar", "16000",
            "-ac", "1",
            "-sample_fmt", "s16",
            path,
        ],
        capture_output=True,
        timeout=duration_s + 5,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        print(f"[error] ffmpeg failed: {stderr[:200]}")
        sys.exit(1)
    return path


def enroll(output_path: str = OUT_PATH):
    path = _record(RECORD_SECS, "ENROLLMENT — speak clearly for calibration")
    from tools.voiceprint import enroll as do_enroll
    emb = do_enroll(path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    import numpy as np
    np.save(output_path, emb)
    print(f"\n[voiceprint] saved to {output_path}")
    os.unlink(path)


def verify(voiceprint_path: str = OUT_PATH):
    path = _record(RECORD_SECS, "VERIFICATION — say something to verify")
    from tools.voiceprint import verify as do_verify
    match, sim = do_verify(path, voiceprint_path)
    label = "MATCH" if match else "NO MATCH"
    print(f"\n[voiceprint] {label} — similarity={sim:.4f}")
    os.unlink(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="Verify against existing voiceprint")
    parser.add_argument("--voiceprint", default=OUT_PATH)
    args = parser.parse_args()

    if args.verify:
        if not os.path.exists(args.voiceprint):
            print(f"[error] no voiceprint at {args.voiceprint}. Run without --verify first.")
            sys.exit(1)
        verify(args.voiceprint)
    else:
        enroll(args.voiceprint)
        print("\n[voiceprint] Enrollment complete. Run with --verify to test.")


if __name__ == "__main__":
    main()
