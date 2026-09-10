"""Fetch the Hunyuan3D-2.1 weights into the flat folders ComfyUI scans.

Upstream ships each model as model.fp16.ckpt inside a per-model directory, but
the nodes list ComfyUI's flat models/diffusion_models and models/vae folders, so
the names have to be flattened on the way in.

These destination filenames are what tools/workflows/hunyuan3d_mesh.json asks
for. Change one and you must change the other.

Geometry only. Texturing additionally needs hunyuan3d-paintpbr-v2-1, which is a
much larger download and is deliberately not baked here.
"""

import os
import shutil

from huggingface_hub import hf_hub_download

REPO = "tencent/Hunyuan3D-2.1"
MODELS = [
    ("hunyuan3d-dit-v2-1/model.fp16.ckpt",
     "/comfyui/models/diffusion_models/hunyuan3d-dit-v2-1-fp16.ckpt"),
    ("hunyuan3d-vae-v2-1/model.fp16.ckpt",
     "/comfyui/models/vae/hunyuan3d-vae-v2-1-fp16.ckpt"),
]

for remote, dest in MODELS:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    src = hf_hub_download(REPO, remote)
    shutil.copy(src, dest)
    print(f"download_models: {remote} -> {dest} ({os.path.getsize(dest)} bytes)")
