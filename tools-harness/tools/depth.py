"""
Depth Anything V2 (depth-anything/*-hf) monocular depth estimation, run fully
locally on Apple Silicon (MPS) or CPU via the transformers pipeline.

Lazy-loads the pipeline on first use — importing this module is cheap (no
torch/transformers import at module scope).
"""

from __future__ import annotations

import logging

log = logging.getLogger("depth")


_MODELS = {
    "small": "depth-anything/Depth-Anything-V2-Small-hf",
    "base": "depth-anything/Depth-Anything-V2-Base-hf",
    "large": "depth-anything/Depth-Anything-V2-Large-hf",
    "giant": "depth-anything/Depth-Anything-V2-Giant-hf",
}

_pipeline = None
_pipeline_key = None


def _get_pipeline(model_size: str):
    global _pipeline, _pipeline_key
    if _pipeline is not None and _pipeline_key == model_size:
        return _pipeline

    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"

    try:
        from transformers import pipeline
    except ImportError as e:
        raise RuntimeError(
            f"Missing transformers: {e}. Run: uv pip install --python "
            ".venv/bin/python transformers torch"
        ) from e

    _pipeline = pipeline(
        task="depth-estimation",
        model=_MODELS[model_size],
        device=device,
    )
    _pipeline_key = model_size
    return _pipeline


def _pick_device() -> str:
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def estimate_depth(image_path: str, output_path: str = "",
                   model_size: str = "small") -> str:
    """Estimate per-pixel depth for an image with Depth Anything V2. Returns a
    depth summary and (optionally) writes a normalized depth-map PNG."""
    import time
    from pathlib import Path

    if not Path(image_path).exists():
        return f"[depth] Image not found: {image_path}"

    model_size = (model_size or "small").lower()
    if model_size not in _MODELS:
        return (f"[depth] Unknown model_size {model_size!r}; "
                f"choose one of: {', '.join(_MODELS)}")

    device = _pick_device()
    try:
        pipe = _get_pipeline(model_size)
    except RuntimeError as e:
        return f"[depth] {e}"

    t0 = time.time()
    try:
        result = pipe(str(Path(image_path).resolve()))
    except Exception as e:
        return f"[depth] Inference failed: {e}"
    elapsed = round(time.time() - t0, 1)

    import numpy as np

    depth = result["predicted_depth"]
    if hasattr(depth, "detach"):
        depth = depth.detach().cpu()
    arr = np.asarray(depth, dtype=np.float32)

    dmin, dmax = float(arr.min()), float(arr.max())
    dmean = float(arr.mean())
    span = (dmax - dmin) or 1.0
    norm = ((arr - dmin) / span * 255.0).astype("uint8")

    saved = ""
    if output_path or True:
        if not output_path:
            output_path = str(Path(image_path).with_name(
                f"{Path(image_path).stem}_depth.png"))
        from PIL import Image
        Image.fromarray(norm).save(output_path)
        saved = f"\nDepth map saved: {output_path}"

    return (
        f"[depth] Depth Anything V2 ({model_size}) on {image_path} "
        f"({elapsed}s, {device}): {arr.shape[1]}x{arr.shape[0]}px, "
        f"depth range {dmin:.3f}..{dmax:.3f} (mean {dmean:.3f}, arbitrary scale)"
        f"{saved}"
    )


SCHEMA = {
    "type": "function", "function": {
        "name": "estimate_depth",
        "description": (
            "Estimate monocular depth for an image using Depth Anything V2, running "
            "fully locally (Apple Silicon MPS or CPU, no API). Returns the depth range "
            "and mean, and saves a normalized grayscale depth map PNG (default "
            "{stem}_depth.png) where closer = brighter. Use to measure relative "
            "distances, separate foreground/background, or understand 3D layout. "
            "model_size: small (fastest), base, large, giant (best, slower)."
        ),
        "parameters": {"type": "object", "properties": {
            "image_path": {"type": "string",
                           "description": "Absolute path to the input image."},
            "model_size": {"type": "string",
                           "description": "Model size: small|base|large|giant (default small).",
                           "default": "small"},
            "output_path": {"type": "string",
                            "description": "Where to save the depth map PNG (default: alongside input, {stem}_depth.png).",
                            "default": ""},
        }, "required": ["image_path"]},
    },
}

SCHEMAS = [SCHEMA]