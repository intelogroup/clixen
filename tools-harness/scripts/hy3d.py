#!/usr/bin/env python3
"""Ephemeral Hunyuan3D pod: create it, use it, delete it.

A pod bills per second for as long as it exists, so this tool is built around
short-lived pods — `up` when you start working, `down` the moment you stop.
Nothing here keeps a pod alive for you, and nothing deletes one for you either;
`down` is your job.

    hy3d.py up                    # create pod + install deps (~5-8 min, BILLING STARTS)
    hy3d.py run vase.jpg -o v.glb # image -> mesh (repeat as much as you like)
    hy3d.py status                # what is running, how long, what it has cost
    hy3d.py down                  # delete pod (BILLING STOPS)

Base image is the one behind RunPod template kj1pcha6vo, where nvcc 11.8 matches
a torch built for cu118. Torch's cpp_extension._check_cuda_version hard-raises on
a CUDA major mismatch and has no env-var bypass, so that pairing is load-bearing:
do not "upgrade" the image.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REST = "https://rest.runpod.io/v1"
GQL = "https://api.runpod.io/graphql"
IMAGE = "sovit123/image-to-texture:latest"
STATE = Path.home() / ".hy3d_pod.json"
SSH_KEY = str(Path.home() / ".ssh/id_ed25519")
REMOTE = "/workspace"
FILE_PORT = 8000
CHUNK = 2000     # base64 chars per line, under the PTY canonical-mode input cap
MAX_EDGE = 1024
# Cloudflare rejects urllib's default User-Agent in front of the RunPod API with
# "error code: 1010", so every request claims to be curl.
UA = "curl/8.4.0"

# Ampere/Ada only, cheapest first. cu118 has no kernels for Blackwell (sm_120),
# so an RTX 5090 or PRO 6000 would start fine and then fail at runtime.
GPUS = [
    "NVIDIA RTX A4500",
    "NVIDIA RTX A5000",
    "NVIDIA GeForce RTX 3090",
    "NVIDIA RTX 4000 Ada Generation",
    "NVIDIA GeForce RTX 4090",
    "NVIDIA L4",
    "NVIDIA A40",
    "NVIDIA RTX A6000",
]

BUILD = f"{REMOTE}/build_setup"
SRC = f"{BUILD}/Hunyuan3D-2"
BIREFNET = f"{REMOTE}/BiRefNet"

# The base image ships the CUDA toolchain and /workspace/setup.sh but NOT the
# Python dependencies, the application package, or the compiled CUDA extensions.
# This is setup.sh's sequence with its two environment-destroying steps removed.
#
# Never `pip install -r requirements.txt` (either the app's or BiRefNet's):
#   - The app's leaves torch unpinned, so pip backtracks essentially forever,
#     walking accelerate down to 0.21 and kornia to 0.4.1.
#   - BiRefNet's lists `torch` outright. `--no-deps` does NOT save you: it blocks
#     transitive dependencies but still installs every listed package, so pip
#     happily replaces torch 2.1.0+cu118 with a PyPI build and every CUDA
#     extension compiled above stops loading with
#     "libcublasLt.so.*[0-9] not found in the system path".
# BiRefNet is cloned only for `image_proc`, which is pure Python, so its
# requirements are never installed at all.
#
# gradio is imported at module level by image_to_texture.py above the point where
# the UI is built; scikit-image is commented out of requirements.txt but still
# imported by hy3dgen; meshlib backs FaceReducer, which is on the geometry path,
# not the texture path. numpy stays on 1.x or cu118 torch breaks.
SETUP = f"""
set -e
cd {REMOTE}
TORCH_BEFORE=$(python -c "import torch; print(torch.__version__)")
echo "torch at start: $TORCH_BEFORE"

pip install --no-cache-dir -r {REMOTE}/hunyuan3d_final_req.txt 2>&1 | tail -3
# The image's torch is 2.1.0+cu118, and transformers >= 4.50 calls
# torch.utils._pytree.register_pytree_node, which only became public in torch
# 2.2 — importing it dies with AttributeError before any model loads. 4.49.0 is
# upstream's own pin and imports cleanly against torch 2.1.
pip install --no-cache-dir "transformers==4.49.0" 2>&1 | tail -2
pip install --no-cache-dir gradio scikit-image meshlib hf_transfer \\
    opencv-python-headless "numpy<2" 2>&1 | tail -3
# nvdiffrast is imported at module level by the texture stage, so it blocks even
# a geometry-only run. Pinned: master has broken against this torch before.
pip install --no-cache-dir "git+https://github.com/NVlabs/nvdiffrast.git@v0.3.4" 2>&1 | tail -2

# The application package itself. --no-deps because its setup.py would otherwise
# re-resolve the dependency set we just pinned by hand.
mkdir -p {BUILD}
if [ ! -d {SRC} ]; then
    git clone --depth 1 https://github.com/sovit-123/Hunyuan3D-2.git {SRC} 2>&1 | tail -1
fi
cd {SRC}
pip install --no-cache-dir --no-deps -e . 2>&1 | tail -2

# Two CUDA extensions, compiled against the image's nvcc 11.8 / cu118 torch.
# This is the slow part of a cold boot: roughly 15-25 minutes.
echo "building custom_rasterizer (slow)..."
cd {SRC}/hy3dgen/texgen/custom_rasterizer && python3 setup.py install 2>&1 | tail -3
echo "building differentiable_renderer (slow)..."
cd {SRC}/hy3dgen/texgen/differentiable_renderer && python3 setup.py install 2>&1 | tail -3

# image_proc lives at the BiRefNet repo root and is imported by
# image_to_texture.py. Source only — see the warning above about its requirements.
if [ ! -d {BIREFNET} ]; then
    git clone --depth 1 https://github.com/ZhengPeng7/BiRefNet.git {BIREFNET} 2>&1 | tail -1
fi

export HF_HUB_ENABLE_HF_TRANSFER=1
pkill -f "http.server {FILE_PORT}" 2>/dev/null || true
cd {REMOTE} && nohup python -m http.server {FILE_PORT} >/tmp/http.log 2>&1 &
sleep 1

# The check that matters: nothing above may have replaced torch. If it did, the
# extensions just compiled are dead and the pod is not worth keeping.
cd {REMOTE}
python - <<'PYEOF'
import sys
import torch
print("torch after setup:", torch.__version__)
assert torch.__version__.startswith("2.1.0"), (
    f"torch was replaced during setup: {{torch.__version__}} — "
    "a pip step pulled in a non-cu118 build, the CUDA extensions are now dead")
assert torch.version.cuda.startswith("11.8"), f"not a cu118 torch: {{torch.version.cuda}}"
sys.path.insert(0, {BIREFNET!r})
import cv2, gradio, transformers, nvdiffrast, hy3dgen, image_proc
import custom_rasterizer, mesh_processor
print("deps ok", torch.__version__, "cuda", torch.version.cuda,
      "avail", torch.cuda.is_available())
PYEOF
echo SETUP"_COMPLETE"
"""


def api(path: str, method: str = "GET", body: dict | None = None):
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise SystemExit("RUNPOD_API_KEY not set")
    req = urllib.request.Request(
        f"{REST}/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": UA},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:600]}")


def gql(query: str) -> dict:
    key = os.environ["RUNPOD_API_KEY"]
    req = urllib.request.Request(
        GQL, data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def balance() -> tuple[float, float]:
    m = gql("query { myself { clientBalance currentSpendPerHr } }")["data"]["myself"]
    return m["clientBalance"], m["currentSpendPerHr"]


def ssh_host(pod_id: str) -> str | None:
    """SSH username is the podHostId, not the pod id.

    Using the pod id produces a "container not found" error that reads like the
    pod is down when it is running fine.
    """
    q = 'query { pod(input: {podId: "%s"}) { machine { podHostId } runtime { uptimeInSeconds } } }' % pod_id
    pod = (gql(q).get("data") or {}).get("pod") or {}
    host_id = (pod.get("machine") or {}).get("podHostId")
    return f"{host_id}@ssh.runpod.io" if host_id else None


def ssh(script: str, host: str, timeout: int = 3600) -> str:
    """Run a script on the pod over stdin.

    RunPod's SSH proxy requires a PTY and silently ignores remote commands given
    as arguments, so the script goes in on stdin.
    """
    cmd = ["ssh", "-tt", "-o", "ConnectTimeout=25",
           "-o", "StrictHostKeyChecking=accept-new",
           "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
           "-i", SSH_KEY, host]
    p = subprocess.run(cmd, input=script + "\nexit\n", capture_output=True,
                       text=True, timeout=timeout)
    return p.stdout + p.stderr


def state() -> dict:
    if not STATE.exists():
        raise SystemExit("no pod tracked — run `hy3d.py up` first")
    return json.loads(STATE.read_text())


def live_pods() -> list:
    return api("pods") or []


def glb_stats(path: Path) -> tuple[int, int, int]:
    """(vertices, triangles, materials) from a GLB's JSON chunk."""
    with path.open("rb") as f:
        if f.read(4) != b"glTF":
            return -1, -1, -1
        f.read(8)
        clen, _ = struct.unpack("<II", f.read(8))
        doc = json.loads(f.read(clen))
    v = t = 0
    for m in doc.get("meshes", []):
        for p in m.get("primitives", []):
            v += doc["accessors"][p["attributes"]["POSITION"]]["count"]
            if "indices" in p:
                t += doc["accessors"][p["indices"]]["count"] // 3
    return v, t, len(doc.get("materials", []))


def cmd_up(a) -> None:
    if STATE.exists():
        s = json.loads(STATE.read_text())
        if any(p["id"] == s["id"] for p in live_pods()):
            raise SystemExit(f"pod {s['id']} is already up — `hy3d.py down` first")
        STATE.unlink()

    pod = api("pods", "POST", {
        "name": "hy3d-ephemeral",
        "imageName": a.image,
        "gpuTypeIds": GPUS,
        "gpuCount": 1,
        "containerDiskInGb": a.disk,
        # No network volume on purpose: one mounted at /workspace would shadow
        # the image's own /workspace and hide image_to_texture.py and
        # hunyuan3d_final_req.txt.
        "volumeInGb": 0,
        "ports": [f"{FILE_PORT}/http", "8188/http", "22/tcp"],
        "cloudType": a.cloud,
        "supportPublicIp": True,
    })
    pod_id, cost = pod["id"], pod.get("costPerHr")
    gpu = (pod.get("machine") or {}).get("gpuTypeId")
    STATE.write_text(json.dumps({"id": pod_id, "created": time.time(),
                                 "cost_per_hr": cost}))
    print(f"[up] pod {pod_id}  gpu={gpu}  ${cost}/hr  — BILLING HAS STARTED")

    print("[up] waiting for SSH (usually 1-3 min)...")
    host = None
    deadline = time.time() + 900
    while time.time() < deadline:
        host = ssh_host(pod_id)
        if host and "SSH_OK" in ssh('echo SSH"_OK"', host, timeout=45):
            break
        host = None
        time.sleep(15)
    if not host:
        print(f"[up] SSH never came up. Pod is STILL BILLING: hy3d.py down")
        raise SystemExit(1)
    print(f"[up] ssh ready: {host}")

    if a.no_setup:
        s = json.loads(STATE.read_text())
        s["host"] = host
        STATE.write_text(json.dumps(s))
        print(f"[up] --no-setup: skipping dependency install. ssh host: {host}")
        print(f"[up] when finished:  hy3d.py down   (${cost}/hr until you do)")
        return

    # Cold boot compiles two CUDA extensions from source; there is no shortcut
    # short of baking them into an image.
    print("[up] installing dependencies + compiling CUDA extensions (~20-30 min)...")
    out = ssh(SETUP, host, timeout=3600)
    if "SETUP_COMPLETE" not in out:
        print(out[-2500:])
        print("[up] setup FAILED. Pod is STILL BILLING: hy3d.py down")
        raise SystemExit(1)
    for line in out.splitlines():
        if line.startswith("deps ok"):
            print(f"[up] {line.strip()}")

    s = json.loads(STATE.read_text())
    s["host"] = host
    STATE.write_text(json.dumps(s))
    print(f"[up] READY.  hy3d.py run IMAGE -o OUT.glb")
    print(f"[up] when finished:  hy3d.py down   (${cost}/hr until you do)")


def cmd_run(a) -> None:
    s = state()
    pod_id = s["id"]
    host = s.get("host") or ssh_host(pod_id)
    if not host:
        raise SystemExit("cannot resolve pod ssh host — is the pod up?")

    from PIL import Image
    im = Image.open(a.image).convert("RGB")
    im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    blob = buf.getvalue()
    print(f"[in] {Path(a.image).name} {im.size[0]}x{im.size[1]} {len(blob)/1024:.0f}KB")

    # Upload over SSH: fine at this size. Large files come back over HTTP instead,
    # because base64 through the proxy corrupts above roughly a megabyte.
    b64 = base64.b64encode(blob).decode()
    want = hashlib.sha256(blob).hexdigest()
    lines = [f"rm -f {REMOTE}/input.b64"]
    lines += [f"printf '%s' '{b64[i:i + CHUNK]}' >> {REMOTE}/input.b64"
              for i in range(0, len(b64), CHUNK)]
    lines += [f"base64 -d {REMOTE}/input.b64 > {REMOTE}/input.jpg",
              f"rm -f {REMOTE}/input.b64",
              f"echo SHA=$(sha256sum {REMOTE}/input.jpg | cut -d' ' -f1)"]
    got = re.search(r"SHA=([0-9a-f]{64})", ssh("\n".join(lines), host, timeout=600))
    if not got or got.group(1) != want:
        raise SystemExit("upload checksum mismatch")
    print("[up] image uploaded, checksum verified")

    # image_to_texture.py builds a Gradio app and calls demo.launch(), which
    # blocks forever; everything above `with gr.Blocks()` is the model setup.
    runner = f'''
import sys, os, glob, time
os.chdir("{REMOTE}")
# image_proc is at the BiRefNet repo root, not installed as a package.
sys.path.insert(0, "{BIREFNET}")
sys.argv = ["image_to_texture.py"]
src = open("image_to_texture.py").read()
src = src[:src.index("with gr.Blocks()")]
g = {{"__name__": "__main__"}}
exec(compile(src, "image_to_texture.py", "exec"), g)
cutoff = time.time()
g["image_to_3d"]({a.prompt!r}, "{REMOTE}/input.jpg", {bool(a.texture)})
files = [p for p in glob.glob("outputs/**/*.glb", recursive=True)
         if os.path.getmtime(p) >= cutoff]
print("PICKED=" + (os.path.relpath(max(files, key=os.path.getmtime), "{REMOTE}")
                   if files else "NONE"))
'''
    script = (f"cd {REMOTE}\n"
              f"export HF_HUB_ENABLE_HF_TRANSFER=1\n"
              f"cat > /tmp/_run.py << 'PYEOF'\n{runner}\nPYEOF\n"
              f"python /tmp/_run.py 2>&1 | tail -30\n")
    print("[run] generating (first run also downloads weights)...")
    out = ssh(script, host, timeout=3600)
    picked = re.search(r"PICKED=([^\s]+\.glb)", out)
    if not picked:
        print(out[-2500:])
        raise SystemExit("generation failed")
    rel = picked.group(1)
    print(f"[run] produced {rel}")

    url = f"https://{pod_id}-{FILE_PORT}.proxy.runpod.net/{rel}"
    with urllib.request.urlopen(url, timeout=600) as r:
        a.out.write_bytes(r.read())
    v, t, mats = glb_stats(a.out)
    print(f"[out] {a.out}  {a.out.stat().st_size} bytes")
    print(f"[check] {v} vertices, {t} triangles, {mats} materials")
    if v <= 0 or t <= 0:
        raise SystemExit("GLB has no geometry")
    print("[PASS] pod still running — `hy3d.py down` when finished")


def cmd_status(a) -> None:
    pods = live_pods()
    bal, spend = balance()
    if not pods:
        print("[status] no pods running")
    for p in pods:
        print(f"[status] {p['id']}  {p.get('desiredStatus')}  ${p.get('costPerHr')}/hr  "
              f"gpu={(p.get('machine') or {}).get('gpuTypeId')}")
    if STATE.exists():
        s = json.loads(STATE.read_text())
        hrs = (time.time() - s["created"]) / 3600
        print(f"[status] tracked pod {s['id']} alive {hrs:.2f}h "
              f"≈ ${hrs * (s.get('cost_per_hr') or 0):.2f} so far")
    print(f"[status] balance ${bal:.4f}   spend ${spend}/hr")
    if spend:
        print("[status] SPEND IS NOT ZERO — something is billing right now")


def cmd_down(a) -> None:
    pod_id = a.pod or state().get("id")
    api(f"pods/{pod_id}", "DELETE")
    time.sleep(4)
    remaining = [p["id"] for p in live_pods()]
    STATE.unlink(missing_ok=True)
    bal, spend = balance()
    print(f"[down] deleted {pod_id}")
    print(f"[down] pods remaining: {remaining or 'none'}")
    print(f"[down] balance ${bal:.4f}   spend ${spend}/hr")
    if spend or remaining:
        print("[down] WARNING: something is still billing — check hy3d.py status")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="create the pod and install deps (starts billing)")
    up.add_argument("--cloud", default="SECURE", choices=["SECURE", "COMMUNITY"])
    up.add_argument("--image", default=IMAGE, help="container image to boot")
    up.add_argument("--disk", type=int, default=60, help="container disk in GB")
    up.add_argument("--no-setup", action="store_true",
                    help="boot only, skip the dependency install (for evaluating an image)")
    up.set_defaults(fn=cmd_up)

    run = sub.add_parser("run", help="image -> mesh on the running pod")
    run.add_argument("image", type=Path)
    run.add_argument("-o", "--out", type=Path, default=Path("out.glb"))
    run.add_argument("--texture", action="store_true", help="also run the texture stage")
    run.add_argument("--prompt", default="", help="object filter, e.g. 'cup, spoon'")
    run.set_defaults(fn=cmd_run)

    st = sub.add_parser("status", help="what is running and what it costs")
    st.set_defaults(fn=cmd_status)

    dn = sub.add_parser("down", help="delete the pod (stops billing)")
    dn.add_argument("--pod", help="pod id (defaults to the tracked pod)")
    dn.set_defaults(fn=cmd_down)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
