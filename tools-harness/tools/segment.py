"""
SAM3 (facebook/sam3) text-prompt image segmentation, run locally on Apple
Silicon (MPS) or CPU.

Lazy-loads the model on first use — importing this module is cheap (no torch/
sam3 import at module scope) so harness startup stays fast.

Model weights are gated on HuggingFace: the first run needs the user to accept
access to `facebook/sam3` at huggingface.co logged in with the account whose
HF token is present in the environment.

Note: SAM 3.1 (facebook/sam3.1) is a video-only Object Multiplex release with
no image checkpoint — image segmentation targets the SAM3 image model.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("segment")


# ---------------------------------------------------------------------------
# Non-CUDA compat patches (verified on this Mac, MPS + CPU)
# ---------------------------------------------------------------------------

def _apply_non_cuda_patches() -> None:
    """Monkeypatch sam3 internals so the image model builds/runs without CUDA.

    sam3 assumes a CUDA GPU: triton fused kernels, hardcoded ``device="cuda"``
    construction sites, and bf16 autocast all break on CPU/MPS. Each patch is
    scoped to what the image (Sam3Processor) path touches — video/tracking
    paths are left untouched. Verified end-to-end 2026-08-18 on this repo's
    .venv (CPython 3.14, torch 2.13, no CUDA).
    """
    import sys
    import types
    from contextlib import nullcontext

    import torch

    def _make_pkg(name):
        mod = types.ModuleType(name)
        mod.__path__ = []
        sys.modules[name] = mod
        return mod

    # --- triton stub (no wheels for macOS cp3.14) --------------------------
    triton = _make_pkg("triton")
    backends = _make_pkg("triton.backends")
    compiler_pkg = _make_pkg("triton.compiler")
    compiler_mod = types.ModuleType("triton.compiler.compiler")
    backends_compiler = types.ModuleType("triton.backends.compiler")

    class AttrsDescriptor:
        def __init__(self, **kw):
            pass

    for m in (backends_compiler, compiler_mod):
        m.AttrsDescriptor = AttrsDescriptor

    class Backend:
        def __init__(self, *a, **k):
            self.target = None

        def __getattr__(self, n):
            return None

    class Compiler:
        def __init__(self, *a, **k):
            self.target = None

        def __getattr__(self, n):
            return None

    backends_compiler.Compiler = Compiler
    backends_compiler.Backend = Backend
    compiler_mod.Compiler = Compiler
    compiler_mod.AttrsDescriptor = AttrsDescriptor
    sys.modules["triton.compiler.compiler"] = compiler_mod
    sys.modules["triton.backends.compiler"] = backends_compiler
    triton.backends = backends
    backends.compiler = backends_compiler
    triton.compiler = compiler_pkg
    compiler_pkg.compiler = compiler_mod

    tl = types.ModuleType("triton.language")
    _dtype = type("dtype", (), {})
    for n in ("dtype", "int1", "int8", "int16", "int32", "int64",
              "uint8", "float16", "float32", "float64", "bool"):
        setattr(tl, n, _dtype if n == "dtype" else type(n, (), {}))
    tl.constexpr = lambda x: x
    tl.arange = lambda *a: None
    tl.full = lambda *a, **k: None
    tl.load = lambda *a, **k: None
    tl.store = lambda *a, **k: None
    tl.jit = lambda f: f
    tl.program_id = lambda *a: 0
    sys.modules["triton.language"] = tl
    triton.language = tl
    triton.jit = lambda f: f
    triton.autotune = lambda *a, **k: (lambda f: f)
    triton.heuristics = lambda *a, **k: (lambda f: f)
    triton.next_power_of_2 = lambda x: x

    # --- distance-transform (edt) uses triton only in tracker path ----------
    def _edt_cpu(data):
        import scipy.ndimage as ndi
        import numpy as np
        arr = data.detach().float().cpu().numpy()
        return torch.from_numpy(
            ndi.distance_transform_edt(arr != 0).astype("float32")
        ).to(data.device)

    import sam3.model.edt as _edt_mod
    _edt_mod.edt_triton = _edt_cpu

    # --- PositionEmbeddingSine hardcodes device="cuda" at construction -------
    import sam3.model.position_encoding as _pe

    _orig_init = _pe.PositionEmbeddingSine.__init__

    def _pe_init_patched(self, num_pos_feats, temperature=10000, normalize=True,
                         scale=None, precompute_resolution=None):
        if precompute_resolution is None:
            _orig_init(self, num_pos_feats, temperature, normalize, scale, None)
            return
        _orig_zeros = torch.zeros
        torch.zeros = lambda *a, **k: _orig_zeros(
            *a, device="cpu", **{kk: vv for kk, vv in k.items() if kk != "device"}
        )
        try:
            _orig_init(self, num_pos_feats, temperature, normalize, scale,
                       precompute_resolution)
        finally:
            torch.zeros = _orig_zeros

    _pe.PositionEmbeddingSine.__init__ = _pe_init_patched

    _orig_forward = _pe.PositionEmbeddingSine.forward

    def _pe_forward_patched(self, x):
        return _orig_forward(self, x).to(x.device)

    _pe.PositionEmbeddingSine.forward = _pe_forward_patched

    # --- TransformerDecoder._get_coords build call passes device="cuda" -----
    import sam3.model.decoder as _dec

    _orig_coords = _dec.TransformerDecoder._get_coords

    def _coords_patched(H, W, device=None):
        if device == "cuda" or device is None:
            return _orig_coords(H, W, "cpu")
        return _orig_coords(H, W, device)

    _dec.TransformerDecoder._get_coords = staticmethod(_coords_patched)

    _orig_rpb = _dec.TransformerDecoder._get_rpb_matrix

    def _rpb_patched(self, reference_boxes, feat_size):
        H, W = feat_size
        dev = reference_boxes.device
        if (self.compilable_cord_cache is not None
                and self.compilable_cord_cache[0].device != dev):
            self.compilable_cord_cache = _orig_coords(H, W, dev)
            self.compilable_stored_size = (H, W)
        return _orig_rpb(self, reference_boxes, feat_size)

    _dec.TransformerDecoder._get_rpb_matrix = _rpb_patched

    # --- bf16 autocast on CUDA must not fire on non-CUDA builds --------------
    # Used both as `with torch.autocast(...)` and as `@torch.autocast(...)`
    # decorator, so the replacement must be callable AND a context manager.
    _orig_autocast = torch.autocast

    class _NoAutocast:
        def __call__(self, f):
            return f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _autocast_patched(*args, **kwargs):
        dt = kwargs.get("device_type") or (args[0] if args else None)
        if dt == "cuda" and not torch.cuda.is_available():
            return _NoAutocast()
        return _orig_autocast(*args, **kwargs)

    torch.autocast = _autocast_patched

    # --- pin_memory() .to(mps) raises on non-CUDA; make it a no-op -----------
    torch.Tensor.pin_memory = lambda self: self

    # --- fused addmm casts to bf16 unconditionally; use plain F.linear -------
    import torch.nn.functional as _F
    import sam3.perflib.fused as _fused

    def _addmm_act_patched(activation, linear, mat1):
        y = _F.linear(mat1, linear.weight, linear.bias)
        if activation in [torch.nn.functional.relu, torch.nn.ReLU]:
            return _F.relu(y)
        if activation in [torch.nn.functional.gelu, torch.nn.GELU]:
            return _F.gelu(y)
        raise ValueError(f"Unexpected activation {activation}")

    _fused.addmm_act = _addmm_act_patched
    import sam3.model.vitdet as _vitdet
    _vitdet.addmm_act = _addmm_act_patched


# ---------------------------------------------------------------------------
# Lazy model singleton
# ---------------------------------------------------------------------------

_model = None


def _get_model(device: str):
    """Build (once) the SAM3 image model on `device`, loading gated weights."""
    global _model
    if _model is not None:
        return _model

    import torch

    _apply_non_cuda_patches()

    try:
        from sam3.model_builder import build_sam3_image_model
    except ImportError as e:
        raise RuntimeError(
            f"samtok missing: {e}. Run: uv pip install --python .venv/bin/python "
            "'git+https://github.com/facebookresearch/sam3.git' --no-deps"
        ) from e

    try:
        model = build_sam3_image_model(device="cpu", load_from_HF=True)
    except Exception as e:
        if "GatedRepoError" in type(e).__name__ or "gated" in str(e).lower():
            raise RuntimeError(
                "SAM3 weights are gated on HuggingFace. Open "
                "https://huggingface.co/facebook/sam3 and click 'Agree and "
                "access repository' while logged in as the account whose HF "
                "token is in the environment (HF_TOKEN / ~/.cache/huggingface/"
                "token), then retry."
            ) from e
        raise

    model = model.to(device)
    model.eval()
    _model = (model, device)
    return _model


def _pick_device() -> str:
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

def _segment_prompt(image_path: str, prompt: str = "object"):
    """Run SAM3 text-prompt segmentation. Returns ``(image, masks, boxes,
    scores, device, elapsed_s)`` where ``masks`` is [N,1,H,W] float (image
    resolution), ``boxes`` is [N,4] and ``scores`` is a list of floats."""
    import time

    device = _pick_device()
    model, _ = _get_model(device)

    try:
        from sam3.model.sam3_image_processor import Sam3Processor
        from PIL import Image
    except ImportError as e:
        raise RuntimeError(f"Missing dep: {e}") from e

    proc = Sam3Processor(model, device=device)
    image = Image.open(image_path).convert("RGB")

    t0 = time.time()
    state = proc.set_image(image)
    out = proc.set_text_prompt(state=state, prompt=prompt.strip() or "object")
    elapsed = round(time.time() - t0, 1)

    import torch
    masks = out["masks"].detach().cpu()
    boxes = out["boxes"].detach().cpu()
    scores = out["scores"].detach().cpu().tolist()
    return image, masks, boxes, scores, device, elapsed


def segment_image(image_path: str, prompt: str = "", output_path: str = "") -> str:
    """Segment an image by a free-text prompt using SAM3. Returns a summary of
    detected masks and (optionally) writes a visual overlay PNG."""
    from pathlib import Path

    if not Path(image_path).exists():
        return f"[segment] Image not found: {image_path}"

    try:
        image, masks, boxes, scores, device, elapsed = _segment_prompt(image_path, prompt)
    except RuntimeError as e:
        return f"[segment] {e}"

    n = masks.shape[0]
    if n == 0:
        return (
            f"[segment] SAM3 found no regions for prompt {prompt!r} on "
            f"{image_path} ({elapsed}s, {device}). Try a more descriptive prompt."
        )

    if not output_path:
        output_path = str(Path(image_path).with_name(
            f"{Path(image_path).stem}_segment.png"))
    try:
        _write_overlay(image, masks, boxes, scores, output_path)
        saved = f"\nOverlay saved: {output_path}"
    except Exception as e:
        saved = f"\n(overlay write failed: {e})"

    lines = [
        f"[segment] SAM3 found {n} region(s) for prompt {prompt!r} "
        f"({elapsed}s, {device}):",
    ]
    for i in range(n):
        x1, y1, x2, y2 = boxes[i].tolist()
        lines.append(
            f"  {i+1}. score={scores[i]:.2f} box=({int(x1)},{int(y1)})-"
            f"({int(x2)},{int(y2)}) area={int((x2-x1)*(y2-y1))}px"
        )
    lines.append(saved)
    return "\n".join(lines)


def _write_overlay(image, masks, boxes, scores, output_path):
    import torch
    from PIL import Image as _PIL, ImageDraw

    w, h = image.size
    overlay = _PIL.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    palette = [(255, 59, 48, 120), (0, 122, 255, 120), (52, 199, 89, 120),
               (255, 149, 0, 120), (175, 82, 222, 120), (255, 204, 0, 120)]
    n = masks.shape[0]
    for i in range(n):
        m = masks[i, 0]  # [H, W]
        if m.shape != (h, w):
            import torch.nn.functional as _F
            m = _F.interpolate(
                m.float().unsqueeze(0).unsqueeze(0), size=(h, w),
                mode="bilinear", align_corners=False,
            ).squeeze(0).squeeze(0)
        color = palette[i % len(palette)]
        mask_img = _PIL.fromarray((m.numpy() > 0.5).astype("uint8") * 255)
        if mask_img.size != overlay.size:
            mask_img = mask_img.resize((w, h))
        overlay.paste(_PIL.new("RGBA", (w, h), color), (0, 0), mask_img)
        x1, y1, x2, y2 = boxes[i].tolist()
        draw.rectangle([x1, y1, x2, y2], outline=color[:3], width=3)
        draw.text((x1, max(0, y1 - 14)), f"{scores[i]:.2f}", fill=color[:3])

    composite = image.convert("RGBA")
    composite = _PIL.alpha_composite(composite, overlay)
    composite.convert("RGB").save(output_path)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = {
    "type": "function", "function": {
        "name": "segment_image",
        "description": (
            "Segment objects in an image by a free-text prompt using Meta's SAM3, "
            "running fully locally (Apple Silicon MPS or CPU, no API). Returns each "
            "detected region's confidence score and bounding box, and saves a visual "
            "overlay PNG (default {stem}_segment.png). Example prompts: 'person', "
            "'the red car', 'eyes', 'brightest region'. Weights come from the gated "
            "huggingface.co/facebook/sam3 repo (one-time access grant needed)."
        ),
        "parameters": {"type": "object", "properties": {
            "image_path": {"type": "string",
                           "description": "Absolute path to the input image."},
            "prompt": {"type": "string",
                       "description": "Free-text description of the region to segment (e.g. 'person').",
                       "default": ""},
            "output_path": {"type": "string",
                            "description": "Where to save the overlay PNG (default: alongside input, {stem}_segment.png).",
                            "default": ""},
        }, "required": ["image_path"]},
    },
}

SCHEMAS = [SCHEMA]