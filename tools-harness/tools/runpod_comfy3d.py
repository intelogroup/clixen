"""Hunyuan3D image-to-3D via a RunPod Serverless worker-comfyui endpoint.

One tool (`runpod_comfy3d_generate`) posts a ComfyUI workflow plus the source
image, waits for the job, and writes the returned GLB to disk.

The endpoint runs the image built in runpod-hunyuan3d-comfy/. It scales to zero,
so an idle endpoint costs nothing; the first request after an idle period pays a
cold start while the image is pulled.

Auth is RUNPOD_API_KEY, plus RUNPOD_COMFY3D_ENDPOINT_ID naming the endpoint.

Alternative: tools/tripo3d.py is a hosted API with no infrastructure to keep
working. Prefer it unless per-mesh cost or output licensing argues otherwise.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

_BASE = "https://api.runpod.ai/v2"
_POLL_INTERVAL_S = 5.0
_WORKFLOW = os.path.join(os.path.dirname(__file__), "workflows", "hunyuan3d_mesh.json")
# The node that reads the uploaded file, and the workflow key holding its name.
_IMAGE_NODE = "1"
_UPLOAD_NAME = "input.png"

RUNPOD_COMFY3D_SCHEMA = {
    "type": "function",
    "function": {
        "name": "runpod_comfy3d_generate",
        "description": (
            "Generate a 3D mesh (GLB) from a single image using self-hosted "
            "Hunyuan3D-2.1 on a RunPod serverless endpoint. Geometry only, no "
            "texture. Saves the mesh to disk and returns its path. Slower than "
            "tripo3d_generate on a cold start, but runs on our own endpoint."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "Local path to the source image (jpg/png).",
                },
                "output_path": {
                    "type": "string",
                    "description": "Where to write the .glb. Defaults to alongside the input image.",
                },
                "steps": {
                    "type": "integer",
                    "description": "Diffusion steps, 1-100 (default 30). Higher is slower and more detailed.",
                    "default": 30,
                },
                "seed": {
                    "type": "integer",
                    "description": "Random seed (default 0).",
                    "default": 0,
                },
                "max_facenum": {
                    "type": "integer",
                    "description": "Face budget for the decimated mesh (default 40000).",
                    "default": 40000,
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "Max seconds to wait, including cold start (default 900, max 1800).",
                    "default": 900,
                },
            },
            "required": ["image"],
        },
    },
}


def _build_workflow(steps: int, seed: int, max_facenum: int) -> dict:
    """Load the API-format workflow and apply per-request parameters."""
    with open(_WORKFLOW) as fh:
        wf = json.load(fh)
    wf = copy.deepcopy(wf)
    wf[_IMAGE_NODE]["inputs"]["image"] = _UPLOAD_NAME
    wf["2"]["inputs"]["steps"] = max(1, min(steps, 100))
    wf["2"]["inputs"]["seed"] = seed
    wf["5"]["inputs"]["max_facenum"] = max(1, max_facenum)
    return wf


def _decode_output(images: list) -> tuple[bytes | None, str | None]:
    """Pull the mesh bytes out of the handler's response list.

    The handler labels every collected output "images" regardless of type; our
    build folds the GLB in there (see runpod-hunyuan3d-comfy/patch_handler.py).
    An entry carries either inline base64 or an S3 URL, depending on how the
    endpoint is configured.
    """
    if not images:
        return None, "endpoint returned no outputs — the workflow produced nothing"
    entry = images[0]
    if isinstance(entry, str):
        return base64.b64decode(entry), None
    if entry.get("data"):
        return base64.b64decode(entry["data"]), None
    url = entry.get("url")
    if url:
        try:
            r = requests.get(url, timeout=300)
            r.raise_for_status()
        except requests.RequestException as e:
            return None, f"could not download result from {url}: {e}"
        return r.content, None
    return None, f"unrecognised output entry: {str(entry)[:200]}"


def runpod_comfy3d_generate(
    image: str | None = None,
    output_path: str | None = None,
    steps: int = 30,
    seed: int = 0,
    max_facenum: int = 40000,
    wait_seconds: int = 900,
) -> str:
    api_key = os.environ.get("RUNPOD_API_KEY", "").strip()
    endpoint = os.environ.get("RUNPOD_COMFY3D_ENDPOINT_ID", "").strip()
    if not api_key:
        return "[runpod_comfy3d error] RUNPOD_API_KEY not set in tools-harness/.env"
    if not endpoint:
        return (
            "[runpod_comfy3d error] RUNPOD_COMFY3D_ENDPOINT_ID not set in "
            "tools-harness/.env — deploy runpod-hunyuan3d-comfy/ and add its endpoint id"
        )
    if not image:
        return "[runpod_comfy3d error] image is required."
    if not os.path.exists(image):
        return f"[runpod_comfy3d error] image not found: {image}"

    try:
        with open(image, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
    except OSError as e:
        return f"[runpod_comfy3d error] cannot read {image}: {e}"

    payload = {
        "input": {
            "workflow": _build_workflow(steps, seed, max_facenum),
            "images": [{"name": _UPLOAD_NAME, "image": f"data:image/png;base64,{b64}"}],
        }
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    # Always async: a mesh takes minutes, and a cold start adds the image pull on
    # top, so /runsync would time out on exactly the requests that matter.
    try:
        r = requests.post(f"{_BASE}/{endpoint}/run", headers=headers, json=payload, timeout=120)
    except requests.RequestException as e:
        return f"[runpod_comfy3d error] submit failed: {e}"
    if r.status_code >= 400:
        return f"[runpod_comfy3d error] submit rejected: HTTP {r.status_code}: {r.text[:300]}"
    job_id = r.json().get("id")
    if not job_id:
        return f"[runpod_comfy3d error] no job id in response: {r.text[:300]}"

    wait_seconds = max(1, min(wait_seconds, 1800))
    deadline = time.time() + wait_seconds
    body: dict = {}
    status = "IN_QUEUE"
    while status in ("IN_QUEUE", "IN_PROGRESS") and time.time() < deadline:
        time.sleep(_POLL_INTERVAL_S)
        try:
            r = requests.get(f"{_BASE}/{endpoint}/status/{job_id}", headers=headers, timeout=60)
        except requests.RequestException as e:
            return f"[runpod_comfy3d error] poll failed for job {job_id}: {e}"
        if r.status_code >= 400:
            return f"[runpod_comfy3d error] poll rejected: HTTP {r.status_code}: {r.text[:300]}"
        body = r.json()
        status = body.get("status", status)

    if status != "COMPLETED":
        if status in ("IN_QUEUE", "IN_PROGRESS"):
            return (
                f"[runpod_comfy3d error] job {job_id} still {status} after {wait_seconds}s. "
                "A cold start pulls a large image; retry or raise wait_seconds."
            )
        return f"[runpod_comfy3d error] job {job_id} ended as {status}: {str(body.get('error'))[:300]}"

    out = body.get("output") or {}
    if out.get("error"):
        return f"[runpod_comfy3d error] workflow failed: {str(out['error'])[:400]}"
    data, err = _decode_output(out.get("images") or [])
    if err:
        return f"[runpod_comfy3d error] {err}"

    dest = output_path or os.path.splitext(image)[0] + ".glb"
    try:
        with open(dest, "wb") as fh:
            fh.write(data)
    except OSError as e:
        return f"[runpod_comfy3d error] cannot write {dest}: {e}"
    return f"Hunyuan3D mesh written to {dest} ({len(data)} bytes, job {job_id})"


if __name__ == "__main__":
    # Offline self-check: everything wrong-able without an endpoint is workflow
    # construction, output decoding, and the guard order.
    wf = _build_workflow(steps=7, seed=42, max_facenum=1234)
    assert wf["2"]["inputs"]["steps"] == 7
    assert wf["2"]["inputs"]["seed"] == 42
    assert wf["5"]["inputs"]["max_facenum"] == 1234
    assert wf[_IMAGE_NODE]["inputs"]["image"] == _UPLOAD_NAME
    # steps is clamped to the node's declared 1-100 range.
    assert _build_workflow(999, 0, 40000)["2"]["inputs"]["steps"] == 100
    # The template on disk must not be mutated by a request.
    assert json.load(open(_WORKFLOW))["2"]["inputs"]["steps"] == 30
    # Every node reference must point at a node that exists.
    for nid, node in wf.items():
        for v in node["inputs"].values():
            if isinstance(v, list):
                assert v[0] in wf, f"node {nid} references missing node {v[0]}"

    assert _decode_output([])[1].startswith("endpoint returned no outputs")
    raw = base64.b64encode(b"glTF-bytes").decode()
    assert _decode_output([raw])[0] == b"glTF-bytes"
    assert _decode_output([{"data": raw}])[0] == b"glTF-bytes"
    assert "unrecognised" in _decode_output([{"filename": "x.glb"}])[1]

    os.environ.pop("RUNPOD_API_KEY", None)
    assert runpod_comfy3d_generate(image="x").startswith("[runpod_comfy3d error] RUNPOD_API_KEY")
    os.environ["RUNPOD_API_KEY"] = "test"
    os.environ.pop("RUNPOD_COMFY3D_ENDPOINT_ID", None)
    assert "RUNPOD_COMFY3D_ENDPOINT_ID" in runpod_comfy3d_generate(image="x")
    os.environ["RUNPOD_COMFY3D_ENDPOINT_ID"] = "test"
    assert "image is required" in runpod_comfy3d_generate()
    assert runpod_comfy3d_generate(image="/nope/missing.png").startswith(
        "[runpod_comfy3d error] image not found")

    print("runpod_comfy3d self-check ok")
