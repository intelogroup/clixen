"""Tripo3D tool — text/image -> 3D mesh via the Tripo openapi v2 REST API.

One tool (`tripo3d_generate`) creates a task, polls it, and returns the result
model URLs. A local image path is uploaded first to obtain a file_token; a public
image URL is passed straight through, since the API accepts either.

Auth is a single `TRIPO_API_KEY` in tools-harness/.env, sent as a bearer token.
Requests go through `requests` — no SDK dependency, since the official one is
async-only and this harness calls tools synchronously.

Chosen over a self-hosted GPU (2026-09-10): a RunPod pod spends 10-30 minutes of
paid boot before the first mesh, which never amortizes at occasional use. See the
runpod-image-to-3d memory for that history.
"""

from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

_BASE = "https://api.tripo3d.ai/v2/openapi"
_POLL_INTERVAL_S = 5.0
_TERMINAL = {"success", "failed", "banned", "expired", "cancelled", "unknown"}

TRIPO3D_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tripo3d_generate",
        "description": (
            "Generate a 3D mesh from a text prompt or an image via Tripo3D. "
            "Creates the task, polls until done, and returns downloadable model URLs "
            "(GLB). Accepts a local image path or a public image URL. "
            "URLs are short-lived — download what you want to keep."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the 3D asset (required if image not given).",
                },
                "image": {
                    "type": "string",
                    "description": (
                        "Local image file path or public image URL to convert to 3D "
                        "(alternative to prompt; cannot combine with prompt)."
                    ),
                },
                "texture": {
                    "type": "boolean",
                    "description": "Generate a texture as well as geometry (default true). Costs more credits.",
                    "default": True,
                },
                "model_version": {
                    "type": "string",
                    "description": "Tripo model version, e.g. 'v2.5-20250123'. Omit to let the server pick its default.",
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "Maximum time in seconds to wait for completion (default 300, max 900).",
                    "default": 300,
                },
            },
            "required": [],
        },
    },
}


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ.get('TRIPO_API_KEY', '').strip()}"}


def _envelope(r: requests.Response) -> tuple[dict | None, str | None]:
    """Unwrap Tripo's {code, message, data} envelope into (data, error).

    A non-zero `code` is an application-level error and arrives on both 2xx and
    4xx responses, so the HTTP status alone is not enough to tell success apart
    from failure.
    """
    try:
        body = r.json()
    except ValueError:
        return None, f"HTTP {r.status_code}, non-JSON response: {r.text[:300]}"
    if body.get("code") not in (0, None):
        msg = body.get("message", "")
        suggestion = body.get("suggestion") or ""
        return None, f"code {body['code']}: {msg} {suggestion}".strip()
    if r.status_code >= 400:
        return None, f"HTTP {r.status_code}: {r.text[:300]}"
    return body.get("data") or {}, None


def _upload(path: str) -> tuple[str | None, str | None]:
    """Upload a local image, returning (image_token, error)."""
    ext = os.path.splitext(path)[1].lstrip(".").lower() or "jpg"
    try:
        with open(path, "rb") as fh:
            r = requests.post(
                f"{_BASE}/upload",
                headers=_headers(),
                files={"file": (os.path.basename(path), fh, f"image/{ext}")},
                timeout=120,
            )
    except OSError as e:
        return None, f"cannot read {path}: {e}"
    except requests.RequestException as e:
        return None, f"upload failed: {e}"
    data, err = _envelope(r)
    if err:
        return None, f"upload rejected: {err}"
    token = (data or {}).get("image_token")
    return (token, None) if token else (None, f"upload returned no image_token: {data}")


def _file_field(image: str) -> tuple[dict | None, str | None]:
    """Build the task's `file` field from a URL or a local path."""
    ext = os.path.splitext(image)[1].lstrip(".").lower()
    # The API's `type` is the image format, and it only recognises these.
    fmt = ext if ext in ("jpg", "jpeg", "png", "webp") else "jpg"
    if image.startswith(("http://", "https://")):
        return {"type": fmt, "url": image}, None
    if not os.path.exists(image):
        return None, f"image not found: {image}"
    token, err = _upload(image)
    if err:
        return None, err
    return {"type": fmt, "file_token": token}, None


def tripo3d_generate(
    prompt: str | None = None,
    image: str | None = None,
    texture: bool = True,
    model_version: str | None = None,
    wait_seconds: int = 300,
) -> str:
    if not os.environ.get("TRIPO_API_KEY", "").strip():
        return (
            "[tripo3d error] TRIPO_API_KEY not set in tools-harness/.env — "
            "add it and restart. Get one at https://platform.tripo3d.ai/api-keys"
        )
    if not prompt and not image:
        return "[tripo3d error] pass either prompt or image."
    if prompt and image:
        return "[tripo3d error] pass only one of prompt or image, not both."

    if image:
        file_field, err = _file_field(image)
        if err:
            return f"[tripo3d error] {err}"
        task: dict = {"type": "image_to_model", "file": file_field}
    else:
        task = {"type": "text_to_model", "prompt": prompt}
    task["texture"] = bool(texture)
    if model_version:
        task["model_version"] = model_version

    try:
        r = requests.post(f"{_BASE}/task", headers=_headers(), json=task, timeout=60)
    except requests.RequestException as e:
        return f"[tripo3d error] task creation failed: {e}"
    data, err = _envelope(r)
    if err:
        return f"[tripo3d error] task rejected: {err}"
    task_id = (data or {}).get("task_id")
    if not task_id:
        return f"[tripo3d error] no task_id in response: {data}"

    wait_seconds = max(1, min(wait_seconds, 900))
    deadline = time.time() + wait_seconds
    status, last, progress = "queued", {}, 0
    while status not in _TERMINAL and time.time() < deadline:
        time.sleep(_POLL_INTERVAL_S)
        try:
            r = requests.get(f"{_BASE}/task/{task_id}", headers=_headers(), timeout=30)
        except requests.RequestException as e:
            return f"[tripo3d error] poll failed for task {task_id}: {e}"
        last, err = _envelope(r)
        if err:
            return f"[tripo3d error] poll rejected for task {task_id}: {err}"
        last = last or {}
        status = last.get("status", status)
        progress = last.get("progress", progress)

    if status != "success":
        detail = "timed out" if status not in _TERMINAL else (last.get("message") or "")
        return (
            f"[tripo3d error] task {task_id} ended as {status!r} at {progress}%. {detail}"
        ).strip()

    output = last.get("output") or {}
    # pbr_model is the textured mesh; model/base_model are the untextured fallbacks.
    urls = {k: output[k] for k in ("pbr_model", "model", "base_model", "rendered_image")
            if output.get(k)}
    if not urls:
        return f"[tripo3d error] task {task_id} succeeded but returned no model URL: {output}"

    lines = [f"Tripo3D — task {task_id} done:"]
    lines += [f"  {k}: {v}" for k, v in urls.items()]
    return "\n".join(lines)


if __name__ == "__main__":
    # Offline self-check: the parts that are wrong-able without an API key are
    # envelope unwrapping and the URL-vs-path branch.
    class _R:
        def __init__(self, body, status=200):
            self._b, self.status_code, self.text = body, status, str(body)

        def json(self):
            if self._b is None:
                raise ValueError("not json")
            return self._b

    assert _envelope(_R({"code": 0, "data": {"task_id": "t1"}})) == ({"task_id": "t1"}, None)
    assert _envelope(_R({"code": 2000, "message": "no credits"}))[0] is None
    assert "no credits" in _envelope(_R({"code": 2000, "message": "no credits"}))[1]
    # A non-zero code on a 4xx must report the code, not just the HTTP status.
    assert "code 1004" in _envelope(_R({"code": 1004, "message": "bad key"}, 401))[1]
    assert _envelope(_R(None, 500))[1].startswith("HTTP 500")

    assert _file_field("https://x.test/a.png")[0] == {"type": "png", "url": "https://x.test/a.png"}
    # An unrecognised extension falls back to jpg rather than sending it verbatim.
    assert _file_field("https://x.test/a.tiff")[0]["type"] == "jpg"
    assert _file_field("/nope/missing.png")[1].startswith("image not found")

    os.environ.pop("TRIPO_API_KEY", None)
    assert tripo3d_generate(prompt="x").startswith("[tripo3d error] TRIPO_API_KEY not set")
    os.environ["TRIPO_API_KEY"] = "test"
    assert "either prompt or image" in tripo3d_generate()
    assert "not both" in tripo3d_generate(prompt="a", image="b")
    print("tripo3d self-check ok")
