"""Tencent Cloud Hunyuan-3D tool — text/image -> 3D asset via the global "Pro" API.

One tool (`tencent_hunyuan3d_generate`) submits a SubmitHunyuanTo3DProJob request, polls
QueryHunyuanTo3DProJob, and returns the result file URLs. Result files are typed
GIF (turnaround preview) / OBJ (mesh) / Image, each valid 24h.
Auth is SecretId/SecretKey in tools-harness/.env, signed with Tencent Cloud's
TC3-HMAC-SHA256 scheme (stdlib hmac/hashlib — no SDK dependency).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

_HOST = "hunyuan.intl.tencentcloudapi.com"
_ENDPOINT = f"https://{_HOST}"
_SERVICE = "hunyuan"
_VERSION = "2023-09-01"
_REGION = "ap-singapore"
_POLL_INTERVAL_S = 5.0

TENCENT_HUNYUAN3D_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tencent_hunyuan3d_generate",
        "description": (
            "Generate a 3D asset from a text prompt or image via Tencent Cloud Hunyuan-3D. "
            "Submits the job, polls until done, and returns result file URLs (OBJ mesh + GIF preview). "
            "URLs expire after 24h — download what you need to keep."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the 3D asset, max 1024 utf-8 chars (required if image_url not given).",
                },
                "image_url": {
                    "type": "string",
                    "description": "Publicly reachable image URL to convert to 3D (alternative to prompt; cannot combine with prompt).",
                },
                "model": {
                    "type": "string",
                    "enum": ["3.0", "3.1"],
                    "description": "Hunyuan 3D model version (default 3.0).",
                    "default": "3.0",
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


def _creds() -> tuple[str, str]:
    return os.environ.get("SecretId", "").strip(), os.environ.get("SecretKey", "").strip()


def _sign(secret_key: str, date: str, service: str, string_to_sign: str) -> str:
    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    k_service = _hmac(k_date, service)
    k_signing = _hmac(k_service, "tc3_request")
    return _hmac(k_signing, string_to_sign).hex()


def _tc3_request(action: str, payload: dict, secret_id: str, secret_key: str) -> dict:
    timestamp = int(time.time())
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    body = json.dumps(payload, ensure_ascii=False)

    canonical_headers = f"content-type:application/json\nhost:{_HOST}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical_request = "\n".join(
        ["POST", "/", "", canonical_headers, signed_headers, hashed_payload]
    )

    credential_scope = f"{date}/{_SERVICE}/tc3_request"
    hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = "\n".join(
        ["TC3-HMAC-SHA256", str(timestamp), credential_scope, hashed_canonical_request]
    )

    signature = _sign(secret_key, date, _SERVICE, string_to_sign)
    authorization = (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json",
        "Host": _HOST,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": _VERSION,
        "X-TC-Region": _REGION,
    }
    r = requests.post(_ENDPOINT, headers=headers, data=body.encode("utf-8"), timeout=30)
    return r.json()


def tencent_hunyuan3d_generate(
    prompt: str | None = None,
    image_url: str | None = None,
    model: str = "3.0",
    wait_seconds: int = 300,
) -> str:
    secret_id, secret_key = _creds()
    if not secret_id or not secret_key:
        return (
            "[hunyuan3d error] SecretId/SecretKey not set in tools-harness/.env — "
            "add them and restart."
        )
    if not prompt and not image_url:
        return "[hunyuan3d error] pass either prompt or image_url."
    if prompt and image_url:
        return "[hunyuan3d error] pass only one of prompt or image_url, not both."

    submit_payload: dict = {"Model": model}
    if prompt:
        submit_payload["Prompt"] = prompt
    if image_url:
        submit_payload["ImageUrl"] = image_url

    try:
        resp = _tc3_request("SubmitHunyuanTo3DProJob", submit_payload, secret_id, secret_key)
    except Exception as e:
        return f"[hunyuan3d error] submit failed: {e}"

    err = (resp.get("Response") or {}).get("Error")
    if err:
        return f"[hunyuan3d error] {err.get('Code')}: {err.get('Message')}"

    job_id = (resp.get("Response") or {}).get("JobId")
    if not job_id:
        return f"[hunyuan3d error] no JobId in response: {json.dumps(resp)[:500]}"

    wait_seconds = max(1, min(wait_seconds, 900))
    deadline = time.time() + wait_seconds
    status = "WAIT"
    last_resp: dict = {}
    while status in ("WAIT", "RUN") and time.time() < deadline:
        time.sleep(_POLL_INTERVAL_S)
        try:
            last_resp = _tc3_request("QueryHunyuanTo3DProJob", {"JobId": job_id}, secret_id, secret_key)
        except Exception as e:
            return f"[hunyuan3d error] poll failed for job {job_id}: {e}"
        query_err = (last_resp.get("Response") or {}).get("Error")
        if query_err:
            return f"[hunyuan3d error] {query_err.get('Code')}: {query_err.get('Message')}"
        status = (last_resp.get("Response") or {}).get("Status", status)

    if status != "DONE":
        error_msg = (last_resp.get("Response") or {}).get("ErrorMessage", "")
        return f"[hunyuan3d error] job {job_id} ended with status {status!r}. {error_msg}"

    files = (last_resp.get("Response") or {}).get("ResultFile3Ds", [])
    if not files:
        return f"[hunyuan3d error] job {job_id} DONE but no ResultFile3Ds in response."

    lines = [f"Tencent Hunyuan-3D — job {job_id} done:"]
    for f in files:
        lines.append(f"  {f.get('Type', '?')}: {f.get('Url', '')}")
    return "\n".join(lines)
