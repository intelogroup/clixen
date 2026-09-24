"""
Deterministic animation prep for image-to-video (Wan 2.2 I2V on RunPod).

Turns a still image into an "animation payload" a video model consumes to
produce a deterministic motion diff:

  start.png     — the input at the target model resolution
  mask.png      — deterministic subject mask (SAM3, best region, 0.5 threshold)
  zones.png     — start frame + subject outline + head/torso motion zones
  prompt.txt    — fixed-template prompt enumerating ONLY the enabled subtle motions
  manifest.json — full spec: subject mask hash, zone geometry, motion spec,
                  video params (width/height/length/steps/cfg) and a fixed seed

Determinism chain: SAM3 forward pass is deterministic for a fixed image+prompt,
zones are derived from the mask by fixed geometric rules, the prompt is a fixed
template, and the video-model seed defaults to a hash of the image+mask bytes —
so the same prep always yields the same payload, and the same payload + seed on
the video model reproduces the same motion diff.

This payload is NOT a control video: the video model (Wan 2.2 I2V) consumes
start frame + prompt + seed and produces motion within the allowed zones.
"""

from __future__ import annotations

import logging

log = logging.getLogger("prep_animation")

from tools.segment import _segment_prompt  # noqa: E402  (lazy sam3 reuse)


# Zone boundaries are fixed fractions of the subject bounding-box height, so
# they are deterministic across runs.
ZONE_FRACTIONS = {"head": (0.0, 0.30), "torso": (0.30, 0.62)}

MOTION_DESC = {
    "breathing": ("a barely perceptible chest rise and fall from slow "
                  "breathing (torso zone)"),
    "head_sway": ("a very slow, subtle head sway of at most a few pixels "
                  "(head zone)"),
    "blink": "a single soft natural eye blink (head zone)",
    "hair_drift": "one wisp of hair drifting slightly (head zone)",
}

MOTION_ORDER = ("breathing", "head_sway", "blink", "hair_drift")


def _derive_zones(mask):
    """Derive subject bbox + motion zones from a binary subject mask.

    Pure and deterministic: zones are fixed fractions of the subject bbox.
    ``mask`` is a 2D bool/uint8 numpy array. Returns a dict (see manifest) or
    None when the mask is empty.
    """
    import numpy as np

    m = np.asarray(mask, dtype=bool)
    ys, xs = np.where(m)
    if len(ys) == 0:
        return None
    h, w = m.shape
    y1, y2 = int(ys.min()), int(ys.max())
    x1, x2 = int(xs.min()), int(xs.max())
    bh = y2 - y1 + 1
    zones = {}
    for name, (f0, f1) in ZONE_FRACTIONS.items():
        zy1 = y1 + int(bh * f0)
        zy2 = min(h - 1, y1 + int(bh * f1))
        if zy2 < zy1:
            continue
        area = int(m[zy1:zy2 + 1, x1:x2 + 1].sum())
        zones[name] = {
            "bbox": [x1, zy1, x2, zy2],
            "subject_area_px": area,
            "subject_area_frac": round(area / max(1, int(m.sum())), 4),
        }
    return {
        "bbox": [x1, y1, x2, y2],
        "subject_area_px": int(m.sum()),
        "subject_area_frac": round(float(m.mean()), 4),
        "zones": zones,
    }


def _build_prompt(subject_label: str, motions, custom_motion: str = "") -> str:
    """Fixed-template prompt naming exactly the enabled motions."""
    if custom_motion.strip():
        body = custom_motion.strip().rstrip(".")
    else:
        body = "; ".join(MOTION_DESC[m] for m in motions) if motions else \
            "no subject motion at all (static shot)"
    return (
        f"Photorealistic {subject_label} from the reference image. The frame is "
        f"almost a still photo; ONLY these subtle motions are allowed: {body}. "
        "Camera locked, background and the subject's body stay perfectly still. "
        "No large movement, no warping, no morphing."
    )


def _derive_seed(image_bytes, mask_bytes) -> int:
    """Deterministic seed from the image + mask bytes."""
    import hashlib
    return int(hashlib.sha256(image_bytes + mask_bytes).hexdigest()[:8], 16)


def _write_zones_preview(start, mask_np, zones, out_path):
    """Start frame + subject fill + head/torso zone rects."""
    import numpy as np
    from PIL import Image, ImageDraw

    mask_img = Image.fromarray((mask_np.astype(np.uint8)) * 255)
    ov = Image.new("RGBA", start.size, (0, 0, 0, 0))
    ov.paste(Image.new("RGBA", start.size, (255, 255, 255, 90)), (0, 0), mask_img)
    d = ImageDraw.Draw(ov)
    cols = {"head": (255, 59, 48, 255), "torso": (0, 122, 255, 255)}
    for name, z in zones["zones"].items():
        x1, y1, x2, y2 = z["bbox"]
        d.rectangle([x1, y1, x2, y2], outline=cols[name], width=3)
        d.text((x1 + 4, y1 + 2), name, fill=cols[name])
    comp = Image.alpha_composite(start.convert("RGBA"), ov)
    comp.convert("RGB").save(out_path)


def prep_animation(image_path: str, subject_prompt: str = "person",
                   out_dir: str = "", width: int = 768, height: int = 512,
                   length: int = 81, fps: int = 16, steps: int = 8,
                   cfg: float = 2.0, seed: int = 0, custom_motion: str = "",
                   breathing: bool = True, head_sway: bool = True,
                   blink: bool = False, hair_drift: bool = False) -> str:
    """Build a deterministic animation payload (start frame + subject mask +
    motion zones + prompt + fixed seed) for an image-to-video model."""
    import json
    import time
    import hashlib
    from pathlib import Path

    import numpy as np

    src = Path(image_path)
    if not src.exists():
        return f"[prep_animation] Image not found: {image_path}"

    out = Path(out_dir) if out_dir else src.with_name(src.stem + "_anim")
    out.mkdir(parents=True, exist_ok=True)

    try:
        image, masks, boxes, scores, device, elapsed = \
            _segment_prompt(str(src), subject_prompt)
    except RuntimeError as e:
        return f"[prep_animation] {e}"

    n = masks.shape[0]
    if n == 0:
        return (f"[prep_animation] SAM3 found no regions for prompt "
                f"{subject_prompt!r} ({elapsed}s, {device}). Try a more "
                f"descriptive subject_prompt.")

    # Deterministic subject region: highest score, ties broken by first index.
    best = int(max(range(n), key=lambda i: (scores[i], -i)))

    mask_np = masks[best, 0].detach().cpu().numpy()  # full-res [H, W]
    mask_bin = mask_np > 0.5

    from PIL import Image as PILImage

    start = image.convert("RGB").resize((width, height), PILImage.BILINEAR)
    mask_full = PILImage.fromarray(
        (mask_bin.astype(np.uint8) * 255)).resize(
        (width, height), PILImage.NEAREST)
    mask_np = np.asarray(mask_full) > 127

    zones = _derive_zones(mask_np)
    if zones is None:
        return (f"[prep_animation] Subject mask is empty after thresholding "
                f"(score {scores[best]:.2f}); try a different subject_prompt.")

    if not seed:
        seed = _derive_seed(src.read_bytes(), mask_full.tobytes())

    motions = [m for m in MOTION_ORDER if locals()[m]]
    prompt = _build_prompt(subject_prompt.strip() or "subject", motions,
                           custom_motion)

    start.save(out / "start.png")
    mask_full.save(out / "mask.png")
    _write_zones_preview(start, mask_np, zones, out / "zones.png")
    (out / "prompt.txt").write_text(prompt + "\n")

    manifest = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_image": src.name,
        "model_resolution": [width, height],
        "subject": {
            "prompt": subject_prompt,
            "sam3": {"device": device, "elapsed_s": elapsed,
                     "n_regions": n, "best_index": best,
                     "score": round(float(scores[best]), 3)},
            "mask_sha256": hashlib.sha256(mask_full.tobytes()).hexdigest(),
            "bbox": zones["bbox"],
            "area_frac": zones["subject_area_frac"],
        },
        "zones": zones["zones"],
        "motion_spec": {
            "enabled": ["custom"] if custom_motion.strip() else motions,
            "custom_motion": custom_motion.strip() or None,
            "note": "Background (outside the subject mask) is always frozen.",
        },
        "video": {
            "model": "wan-2.2-i2v (runpod serverless)",
            "width": width, "height": height, "length": length, "fps": fps,
            "steps": steps, "cfg": cfg, "seed": seed,
        },
        "prompt": prompt,
        "payload_files": ["start.png", "mask.png", "zones.png",
                          "prompt.txt", "manifest.json"],
    }
    with open(out / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    head = zones["zones"].get("head", {})
    torso = zones["zones"].get("torso", {})
    motion_label = f"custom: {custom_motion.strip()}" if custom_motion.strip() \
        else str(motions)
    return (
        f"[prep_animation] Payload ready for {src.name} ({elapsed}s, {device}):\n"
        f"  start.png       {start.size[0]}x{start.size[1]} (video-model input)\n"
        f"  mask.png        subject mask, sha256 {manifest['subject']['mask_sha256'][:12]}…, "
        f"area {manifest['subject']['area_frac']:.1%}\n"
        f"  zones.png       subject bbox {zones['bbox']}, head zone "
        f"{head.get('bbox')}, torso zone {torso.get('bbox')}\n"
        f"  prompt.txt      {prompt}\n"
        f"  manifest.json   motion spec {motion_label}, seed {seed} "
        f"(same payload+seed ⇒ same motion diff)\n"
        f"Out: {out}"
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = {
    "type": "function", "function": {
        "name": "prep_animation",
        "description": (
            "Build a deterministic animation payload from a still image for an "
            "image-to-video model (Wan 2.2 I2V on RunPod). Segments the subject "
            "locally with SAM3, derives head/torso motion zones from the mask by "
            "fixed geometric rules, and writes start.png, mask.png, zones.png, "
            "prompt.txt and manifest.json to an output dir. The same payload + "
            "the fixed seed in the manifest reproduces the same motion diff on "
            "the video model. Motion toggles (breathing, head_sway, blink, "
            "hair_drift) select which subtle motions the prompt allows; "
            "background is always frozen. This produces a spec + start frame, "
            "not a control video."
        ),
        "parameters": {"type": "object", "properties": {
            "image_path": {"type": "string",
                           "description": "Absolute path to the source still image."},
            "subject_prompt": {"type": "string",
                               "description": "SAM3 text prompt naming the subject to animate (e.g. 'person').",
                               "default": "person"},
            "out_dir": {"type": "string",
                        "description": "Where to write the payload (default: alongside input, {stem}_anim/).",
                        "default": ""},
            "width": {"type": "integer",
                      "description": "Video-model resolution width (default 768).",
                      "default": 768},
            "height": {"type": "integer",
                       "description": "Video-model resolution height (default 512).",
                       "default": 512},
            "length": {"type": "integer",
                       "description": "Video-model frame count (default 81 ≈ 5s).",
                       "default": 81},
            "fps": {"type": "integer",
                    "description": "Nominal output fps (default 16).",
                    "default": 16},
            "steps": {"type": "integer",
                      "description": "Video-model inference steps (default 8).",
                      "default": 8},
            "cfg": {"type": "number",
                    "description": "Video-model guidance scale (default 2.0).",
                    "default": 2.0},
            "seed": {"type": "integer",
                     "description": "Video-model seed; 0 = derived from image+mask hash for reproducibility (default 0).",
                     "default": 0},
            "custom_motion": {"type": "string",
                              "description": "Free-text description of the motion the video should show (e.g. 'the pen rolls down the table and falls off the edge'). When set, it replaces the breathing/head_sway/blink/hair_drift toggles in the prompt. Default empty.",
                              "default": ""},
            "breathing": {"type": "boolean",
                          "description": "Enable subtle chest rise/fall in the torso zone (default true).",
                          "default": True},
            "head_sway": {"type": "boolean",
                          "description": "Enable very slow subtle head sway in the head zone (default true).",
                          "default": True},
            "blink": {"type": "boolean",
                      "description": "Enable a single soft eye blink (default false).",
                      "default": False},
            "hair_drift": {"type": "boolean",
                           "description": "Enable a wisp of hair drifting (default false).",
                           "default": False},
        }, "required": ["image_path"]},
    },
}

SCHEMAS = [SCHEMA]
