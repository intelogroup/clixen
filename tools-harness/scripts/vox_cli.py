#!/usr/bin/env python3
"""vox_cli.py — drive the Vox-style Blender effect stack headless, agent-friendly.

JSON effect spec -> Blender headless render -> ffmpeg encode, in one shot.
Mirrors the fal_cli.py / higgsfield ergonomics so agents (and humans) can
generate Vox-style effect clips by writing a spec.

Usage:
  python3 scripts/vox_cli.py --spec vox_studio/spec.json --out clip.mp4
  python3 scripts/vox_cli.py --text "PREDATOR" --out clip.mp4
  python3 scripts/vox_cli.py --spec custom.json --fps 8 --out choppier.mp4

The spec schema lives in vox_studio/spec.json (default). Only --text/--fps
are overridable on the CLI; anything else edit the spec.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RENDER_SCRIPT = os.path.join(os.path.dirname(HERE), "vox_studio", "render_blender.py")
DEFAULT_SPEC = os.path.join(os.path.dirname(HERE), "vox_studio", "spec.json")

BLENDER_CANDIDATES = [
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "/opt/homebrew/bin/blender",
    "/usr/local/bin/blender",
]


def find_blender() -> str:
    for c in BLENDER_CANDIDATES:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    w = shutil.which("blender")
    if w:
        return w
    raise SystemExit("[vox] Blender not found — install via: brew install --cask blender")


def build_spec(args) -> str:
    spec_path = args.spec or DEFAULT_SPEC
    with open(spec_path) as f:
        spec = json.load(f)
    if args.text is not None:
        spec = copy.deepcopy(spec)
        spec["text"] = args.text
    if args.fps is not None:
        spec = copy.deepcopy(spec)
        spec["fps"] = args.fps
    if args.frames is not None:
        spec = copy.deepcopy(spec)
        spec["duration_frames"] = args.frames
    if not (args.spec or args.text or args.fps or args.frames):
        return spec_path
    fd, tmp = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(spec, f, indent=2)
    return tmp


def main() -> int:
    ap = argparse.ArgumentParser(description="Render Vox-style effect clips headless (Blender + ffmpeg)")
    ap.add_argument("--spec", help="path to effect spec JSON (default: vox_studio/spec.json)")
    ap.add_argument("--text", help="override the text on the card")
    ap.add_argument("--fps", type=int, help="override frame rate (12 = cutting on twos)")
    ap.add_argument("--frames", type=int, help="override frame count")
    ap.add_argument("--out", required=True, help="output mp4 path")
    ap.add_argument("--keep-frames", action="store_true", help="keep the PNG frame sequence")
    args = ap.parse_args()

    blender = find_blender()
    spec_path = build_spec(args)
    work = tempfile.mkdtemp(prefix="vox_")
    frames_dir = os.path.join(work, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    cmd = [blender, "-b", "-P", RENDER_SCRIPT, "--", spec_path, frames_dir]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-4000:], file=sys.stderr)
        print(r.stderr[-4000:], file=sys.stderr)
        print("[vox] Blender render failed", file=sys.stderr)
        shutil.rmtree(work, ignore_errors=True)
        return 1

    with open(spec_path) as f:
        spec = json.load(f)
    fps = spec["fps"]

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    glob_pat = os.path.join(frames_dir, "frame_*.png")
    enc = ["ffmpeg", "-y", "-framerate", str(fps), "-pattern_type", "glob", "-i", glob_pat,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out]
    e = subprocess.run(enc, capture_output=True, text=True)
    if e.returncode != 0:
        print(e.stderr[-3000:], file=sys.stderr)
        print("[vox] ffmpeg encode failed", file=sys.stderr)
        shutil.rmtree(work, ignore_errors=True)
        return 1

    print(f"[vox] {spec.get('text', '?')} @ {fps}fps x {spec.get('duration_frames', '?')} frames -> {out}")
    if args.keep_frames:
        print(f"[vox] frames kept at {frames_dir}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())