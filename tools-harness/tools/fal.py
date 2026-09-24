"""fal.ai tool — run any fal model (text→image, video, audio, upscale, etc.) via the queue API.

One tool (`fal_generate`) submits a request to a fal model/app, polls for completion,
and returns the result (image/video/audio URLs or text). Auth is FAL_KEY (migrated to
Keychain by tools/env_secrets.py like every other provider key).
"""

from __future__ import annotations

import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

API_BASE = "https://queue.fal.run"
_POLL_INTERVAL_S = 5.0

FAL_GENERATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fal_generate",
        "description": (
            "Generate media on fal.ai by running any fal model or app. Pass the model ID "
            "(e.g. 'fal-ai/flux/dev', 'fal-ai/flux/schnell', 'fal-ai/stable-diffusion-v35-large', "
            "'fal-ai/ltx-video', 'fal-ai/whisper', or your own 'username/app') and a prompt or "
            "full input payload. Returns the result — for image models a list of image URLs, for "
            "video/audio a media URL, for text models the text. Media URLs (v3.fal.media) expire "
            "per account settings, so download what you need to keep."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                    "description": "fal model or app id, e.g. 'fal-ai/flux/schnell'.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Text prompt (shorthand for input.prompt).",
                },
                "input": {
                    "type": "object",
                    "description": "Full model input payload (JSON object). Check the model's API page for its schema.",
                    "default": {},
                },
                "image_size": {
                    "type": "string",
                    "enum": ["square_hd", "square", "portrait_4_3", "portrait_16_9", "landscape_4_3", "landscape_16_9"],
                    "description": "Output size for image models. Omit to use the model default.",
                },
                "num_images": {
                    "type": "integer",
                    "description": "Number of images to generate (image models, default 1).",
                    "default": 1,
                },
                "num_inference_steps": {
                    "type": "integer",
                    "description": "Diffusion steps (image models). Omit for model default.",
                },
                "seed": {
                    "type": "integer",
                    "description": "Random seed for reproducibility. Omit for a random seed.",
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "Maximum time in seconds to wait for completion (default 300, max 900).",
                    "default": 300,
                },
            },
            "required": ["model_id"],
        },
    },
}


def _key() -> str:
    return os.environ.get("FAL_KEY", "").strip()


def fal_generate(
    model_id: str,
    prompt: str | None = None,
    input: dict | None = None,
    image_size: str | None = None,
    num_images: int = 1,
    num_inference_steps: int | None = None,
    seed: int | None = None,
    wait_seconds: int = 300,
) -> str:
    key = _key()
    if not key:
        return (
            "[fal error] FAL_KEY not set in environment — add it to tools-harness/.env "
            "and restart, or set it via the Keychain."
        )

    payload = dict(input or {})
    if prompt:
        payload.setdefault("prompt", prompt)
    if image_size:
        payload["image_size"] = image_size
    if num_images is not None and num_images != 1:
        payload["num_images"] = num_images
    if num_inference_steps is not None:
        payload["num_inference_steps"] = num_inference_steps
    if seed is not None:
        payload["seed"] = seed

    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}

    try:
        r = requests.post(f"{API_BASE}/{model_id}", headers=headers, json=payload, timeout=30)
    except Exception as e:
        return f"[fal error] submitting to {model_id!r} failed: {e}"

    try:
        body = r.json()
    except Exception:
        body = {}

    if not body.get("request_id"):
        detail = body.get("detail") or r.text[:300]
        return f"[fal error] {model_id!r} rejected: HTTP {r.status_code} {detail}"

    request_id = body["request_id"]
    status_url = body.get("status_url") or f"{API_BASE}/{model_id}/requests/{request_id}/status"
    response_url = body.get("response_url") or f"{API_BASE}/{model_id}/requests/{request_id}"

    wait_seconds = max(1, min(wait_seconds, 900))
    deadline = time.time() + wait_seconds
    status = "IN_QUEUE"
    last = body
    while status not in ("COMPLETED",) and time.time() < deadline:
        time.sleep(_POLL_INTERVAL_S)
        try:
            s = requests.get(status_url, headers=headers, timeout=30)
            if s.status_code == 200:
                last = s.json()
                status = last.get("status", status)
            else:
                break
        except Exception:
            break

    if status != "COMPLETED":
        err = last.get("error") or last.get("error_type") or status
        return (
            f"[fal error] {model_id!r} request {request_id} ended with status {status!r} "
            f"(error: {err})."
        )

    try:
        res = requests.get(response_url, headers=headers, timeout=30)
        if res.status_code != 200:
            return f"[fal error] fetching result for request {request_id} failed: HTTP {res.status_code}"
        result = res.json()
    except Exception as e:
        return f"[fal error] fetching result for request {request_id} failed: {e}"

    return f"fal {model_id} — request {request_id}\n{json.dumps(result, ensure_ascii=False, default=str)[:6000]}"
