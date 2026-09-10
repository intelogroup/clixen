# Image → 3D on RunPod

**The working path is an on-demand pod, created when you need it and deleted when
you stop.** Serverless was tried twice and abandoned; see "Why not serverless" at
the bottom so nobody spends another day on it.

Verified: `narrow-neck-vase.jpg` → 50,002 vertices / 100,000 triangles in 43 s of
GPU time. Geometry only — the texture stage has never been run, so meshes come
back with `materials = 0`.

## Use it

```sh
export RUNPOD_API_KEY=...            # from tools-harness/.env
cd tools-harness

python scripts/hy3d.py up            # create pod + install deps (~5-8 min) — BILLING STARTS
python scripts/hy3d.py run ~/Downloads/vase.jpg -o vase.glb
python scripts/hy3d.py status        # what is running, how long, what it has cost
python scripts/hy3d.py down          # delete pod — BILLING STOPS
```

`up` and `down` are the whole cost model. A pod bills per second for as long as
it exists, whether or not it is doing anything: nothing in this tool keeps a pod
alive for you, and nothing deletes one for you. Run `down` when you stop working.

`status` is safe to run anytime and reports live spend. If it ever prints
`SPEND IS NOT ZERO` with no pod you expect, something is billing.

Typical cost: roughly $0.26–0.50/hr depending on which GPU you get. A two-hour
session is well under a dollar. Leaving a pod up overnight is what hurts.

## The one thing that makes it work

Image `sovit123/image-to-texture:latest` (the image behind RunPod template
`kj1pcha6vo`). It ships `nvcc` 11.8 against a torch built for CUDA 11.8.

That version match is the whole game. Torch's `cpp_extension._check_cuda_version`
raises a hard `RuntimeError` on a CUDA *major* mismatch, parsing `nvcc --version`
for `release (\d+[.]\d+)`. No environment variable disables it. Every failed
approach in this project's history failed because the toolkit and the torch build
disagreed. Starting from an image where they already agree deletes the entire
problem class. Do not "upgrade" the base image.

GPU choice is restricted to Ampere and Ada on purpose. cu118 has no kernels for
Blackwell (sm_120), so an RTX 5090 or PRO 6000 starts fine and then fails at
runtime.

## Dependencies: the trap that cost the most time

**The base image ships the CUDA toolchain and the application source but NOT the
Python dependencies.** `hy3d.py up` installs them. If you ever do it by hand:

```sh
pip install -r /workspace/hunyuan3d_final_req.txt
pip install gradio scikit-image meshlib hf_transfer opencv-python-headless "numpy<2"
```

- **`hunyuan3d_final_req.txt`, never `requirements.txt`.** The latter pins
  `transformers==4.49.0` while leaving `torch` unpinned, so pip backtracks
  essentially forever, walking `accelerate` down to 0.21 and `timm` to 0.1.1, and
  can replace the cu118 torch everything depends on. The frozen file has 10 pins
  and names no torch, nvdiffrast, or numpy, so it cannot disturb the environment.
- **`gradio`** is imported at module level by `image_to_texture.py` (line 26),
  above the point where the UI is built, so it loads even though no UI is wanted.
- **`scikit-image`** is commented out of `requirements.txt` and still imported by
  `hy3dgen`.
- **`meshlib`** backs `FaceReducer`, which is on the *geometry* path, not the
  texture path. Easy to skip on the assumption it is texture-only. It isn't.
- **`numpy<2`** — numpy 2 breaks the cu118 torch build.
- **`opencv-python-headless`**, not `opencv-python`: the GUI build needs
  `libGL`/`libgthread`, which this image has no reason to carry.

Other runtime notes:

- **`custom_rasterizer` raising `ImportError: libc10.so`** is not a build failure.
  Import `torch` first; the extension links against torch's shared libraries.
- **`nvdiffrast` is imported at module level**, so it blocks even a geometry-only
  run. Install it or stub the import.
- **Install `hf_transfer`** before downloading weights. Measured here: 0.7 MB/s
  single-stream versus 6.4 MB/s with 8 parallel range requests — a 3.8 GB
  checkpoint drops from about 91 minutes to 20 seconds.

## How generation is driven

`image_to_texture.py` builds a Gradio app: `with gr.Blocks()` starts around line
320 and `demo.launch(share=True)` at line 351 blocks forever. Run only the part
before the UI, then call the pipeline directly:

```python
src = open("image_to_texture.py").read()
src = src[:src.index("with gr.Blocks()")]
g = {"__name__": "__main__"}
exec(compile(src, "image_to_texture.py", "exec"), g)
g["image_to_3d"]("", "/workspace/input.jpg", False)  # (text, image_path, do_texture)
```

Only meshes written *after* the call are collected — `outputs/` accumulates
results from earlier runs, so picking the newest file blindly returns stale
geometry.

## RunPod API and SSH gotchas

- **`POST /v1/pods` with an empty body creates a real $0.49/hr pod.** It does not
  validate and reject. Always send an explicit body.
- **Cloudflare rejects urllib's default User-Agent** in front of the RunPod API
  with `error code: 1010`. Send `User-Agent: curl/8.4.0`.
- **No network volume.** A volume mounted at `/workspace` shadows the image's own
  `/workspace`, hiding `image_to_texture.py` and `hunyuan3d_final_req.txt`.
- **SSH username is the `podHostId`**, not the pod id. Using the pod id gives a
  "container not found" error that reads like the pod is down when it is running.
- **A PTY is mandatory** — pass `-tt`.
- **Remote commands are ignored.** `ssh host "cmd"` silently does nothing; send
  the script on stdin.
- **`scp` and `sftp` do not work.** Base64 over the SSH channel corrupts above
  roughly a megabyte, so `hy3d.py` uploads the input image that way (small) but
  pulls meshes back over the pod's HTTP proxy at
  `https://{podId}-8000.proxy.runpod.net/`.
- **Input lines are capped at about 4 KB** by PTY canonical mode; chunk at 2000.
- **The proxy echoes commands back**, so grepping output for a sentinel matches
  the echoed command. Split it remotely — `echo "STAGE2""_FINISHED"`.

## Why not serverless

Two attempts, both abandoned. Endpoints and repo deleted 2026-09-10.

**Attempt 1 — community image `yueqianma/trellis-runpod`.** Jobs stayed
`IN_QUEUE` forever while workers reported `idle`/`ready`. The decisive evidence:
the worker's *system* log showed a clean image pull ending in `worker is ready`,
while its *container* log was empty — the container produced no output and never
registered a handler. Nothing in an opaque third-party image was fixable from
outside it.

**Attempt 2 — our own worker, built by RunPod from a GitHub repo.** This got
much further: jobs were consumed, the handler ran, and failures came back as real
tracebacks. It died of attrition on dependencies (`cv2`, then `gradio`, with
`transformers` also missing) because the base image has no Python deps — the same
trap documented above. It was working toward a fix when the call was made to stop.

Verdict: serverless is achievable but only pays off at steady request volume,
and it needs an image with the dependencies baked in. For occasional sessions the
ephemeral pod is cheaper in both money and attention. If it is ever revisited,
start by baking `hunyuan3d_final_req.txt` into the image, and use a network
volume for weights so cold start does not pay for a 3.8 GB download.

**Do not use `syntech123/worker-trellis:1.6`.** Its image build args contain a
leaked HuggingFace token.
