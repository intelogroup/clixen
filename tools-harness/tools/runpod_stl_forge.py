"""RunPod Serverless STL Forge — image -> 3D-printable STL via Hunyuan3D 2.1 (shape-only).

One tool (`stl_forge_generate`) POSTs to the endpoint's /runsync (falls back to /run + poll
on timeout), returns a presigned stl_url (or inline stl_b64 for small meshes) plus a report
(vertices/faces/watertight/timing). Auth is RUNPOD_API_KEY, endpoint id RUNPOD_STL_FORGE_ENDPOINT_ID
in tools-harness/.env. https://github.com/dranoto/stl-forge
"""

from __future__ import annotations

import os
import time

import requests

_BASE = "https://api.runpod.ai/v2"
_RUNSYNC_WAIT_MS = 300_000  # 5 min, covers cold start per stl-forge README
_POLL_INTERVAL_S = 5.0

STL_FORGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "stl_forge_generate",
        "description": (
            "Convert a single image into a 3D-printable STL file via Hunyuan3D 2.1 "
            "(shape-only) on RunPod Serverless. Returns a presigned stl_url (24h TTL) "
            "plus a report (vertex/face count, watertight check, bbox, timing)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "http(s):// image URL, or a data:image/...;base64,... / raw base64 string.",
                },
                "target_faces": {
                    "type": "integer",
                    "description": "Decimation target, 10000-1000000 (default 100000).",
                    "default": 100000,
                },
                "mc_resolution": {
                    "type": "integer",
                    "description": "Marching Cubes voxel grid resolution, lower = less VRAM/coarser mesh (default 256).",
                    "default": 256,
                },
                "quant": {
                    "type": "string",
                    "enum": ["fp16", "fp8", "int8"],
                    "description": "DiT quantization to reduce VRAM (default fp16).",
                    "default": "fp16",
                },
                "force_inline": {
                    "type": "boolean",
                    "description": "Return stl_b64 inline instead of stl_url. Only safe for <=100k faces.",
                    "default": False,
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "Max time to wait for completion (default 300, max 600).",
                    "default": 300,
                },
            },
            "required": ["image"],
        },
    },
}


def _creds() -> tuple[str, str]:
    return (
        os.environ.get("RUNPOD_API_KEY", "").strip(),
        os.environ.get("RUNPOD_STL_FORGE_ENDPOINT_ID", "").strip(),
    )


def stl_forge_generate(
    image: str,
    target_faces: int = 100000,
    mc_resolution: int = 256,
    quant: str = "fp16",
    force_inline: bool = False,
    wait_seconds: int = 300,
) -> str:
    api_key, endpoint_id = _creds()
    if not api_key or not endpoint_id:
        return (
            "[stl_forge error] RUNPOD_API_KEY / RUNPOD_STL_FORGE_ENDPOINT_ID not set in "
            "tools-harness/.env — add them and restart."
        )
    if not image:
        return "[stl_forge error] image is required (URL or base64)."

    wait_seconds = max(1, min(wait_seconds, 600))
    payload = {
        "input": {
            "image": image,
            "target_faces": target_faces,
            "mc_resolution": mc_resolution,
            "quant": quant,
            "force_inline": force_inline,
        }
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        r = requests.post(
            f"{_BASE}/{endpoint_id}/runsync?wait={_RUNSYNC_WAIT_MS}",
            headers=headers,
            json=payload,
            timeout=wait_seconds + 10,
        )
        resp = r.json()
    except Exception as e:
        return f"[stl_forge error] request failed: {e}"

    status = resp.get("status")
    job_id = resp.get("id", "")

    if status in ("IN_QUEUE", "IN_PROGRESS"):
        deadline = time.time() + wait_seconds
        while status in ("IN_QUEUE", "IN_PROGRESS") and time.time() < deadline:
            time.sleep(_POLL_INTERVAL_S)
            try:
                r = requests.get(f"{_BASE}/{endpoint_id}/status/{job_id}", headers=headers, timeout=30)
                resp = r.json()
            except Exception as e:
                return f"[stl_forge error] poll failed for job {job_id}: {e}"
            status = resp.get("status", status)

    if status != "COMPLETED":
        err = resp.get("error", "")
        return f"[stl_forge error] job {job_id or '?'} ended with status {status!r}. {err}"

    out = resp.get("output", {}) or {}
    if out.get("error"):
        return f"[stl_forge error] handler error: {out['error']}"

    report = out.get("report", {})
    lines = [f"STL Forge — job {job_id or '?'} done:"]
    if out.get("stl_url"):
        lines.append(f"  stl_url: {out['stl_url']}")
    if out.get("stl_b64"):
        lines.append(f"  stl_b64: <{len(out['stl_b64'])} chars inline base64>")
    if out.get("stl_bytes"):
        lines.append(f"  stl_bytes: {out['stl_bytes']}")
    if report:
        lines.append(
            "  report: verts={vertices} faces={faces} watertight={watertight} "
            "bbox={bbox_extents} gen_time_s={generation_time_s} load_time_s={model_load_time_s}".format(
                vertices=report.get("vertices"),
                faces=report.get("faces"),
                watertight=report.get("watertight"),
                bbox_extents=report.get("bbox_extents"),
                generation_time_s=report.get("generation_time_s"),
                model_load_time_s=report.get("model_load_time_s"),
            )
        )
    return "\n".join(lines)
