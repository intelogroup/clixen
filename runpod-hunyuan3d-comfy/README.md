# Hunyuan3D-2.1 on RunPod Serverless

Image-to-3D (geometry, no texture) as a serverless ComfyUI endpoint, built on
[`runpod-workers/worker-comfyui`](https://github.com/runpod-workers/worker-comfyui).
Driven from the harness by `tools/runpod_comfy3d.py`.

## Why an image rather than a pod

Two earlier attempts installed these dependencies on a running, billed pod. A pip
step whose requirements listed `torch` replaced the CUDA-matched wheel, which
orphaned the compiled CUDA extensions; the failure surfaced only at generation
time as `libcublasLt.so.*[0-9] not found in the system path`. Doing the same work
in a Dockerfile makes that a build failure instead — free, and caught before
anything is deployed. The guard at the end of the Dockerfile enforces it.

A pod also spends 10-30 minutes of paid boot before the first mesh. A serverless
endpoint scales to zero and pays only for the cold start plus the job.

## What the build adds

The base image ships the CUDA *runtime*, so there is no `nvcc`. The build adds
the toolkit and `build-essential` (nvcc on its own is not a compiler), installs
the `ComfyUI-Hunyuan3d-2-1` wrapper node, compiles `custom_rasterizer` and
`DifferentiableRenderer`, and bakes the dit and vae weights.

It also patches the handler. The stock handler collects only the `images` key
from each node's output and drops the rest, so the exported GLB would never reach
the caller — `patch_handler.py` folds the node's `3d` output into that same path.

## Build and deploy

Build on `linux/amd64`. Do not cross-build this from an Apple Silicon machine:
it is a large CUDA image with a compile step, and emulation makes that far
slower than building it remotely. Push this directory to a GitHub repository and
deploy it from the RunPod Hub, which builds natively.

Then, in the RunPod console:

- Enable **Refresh Worker** so the worker stops after each job.
- Size the container disk to the baked weights plus headroom.
- Copy the endpoint id into `tools-harness/.env` as `RUNPOD_COMFY3D_ENDPOINT_ID`.

## Scope

Geometry only. The texture pipeline needs the `hunyuan3d-paintpbr-v2-1` weights,
which are not baked here; adding them means another download layer, a larger
image, and the texturing workflow in place of `hunyuan3d_mesh.json`.

The model filenames in the Dockerfile and in
`tools-harness/tools/workflows/hunyuan3d_mesh.json` must agree. Change one and
you must change the other.
