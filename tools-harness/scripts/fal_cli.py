#!/usr/bin/env python3
"""fal.ai CLI — generate image/video/audio and download the result, in one shot.

Standalone wrapper over the fal queue API so future video/image builds can call
fal.ai directly (cheaper per-clip than bundled credit plans) with the same
ergonomics as `higgsfield generate create`.

Usage:
  python3 scripts/fal_cli.py image --prompt "..." --out out.png
  python3 scripts/fal_cli.py video --prompt "..." --image ref.png --duration 10 --out out.mp4
  python3 scripts/fal_cli.py run   --model fal-ai/flux/dev --input req.json --out out.png

Auth: FAL_KEY is read from (in order) the environment, tools-harness/.env, or the
macOS Keychain (service `clixen.secret.FAL_KEY`). Set it once via
`security add-generic-password -s clixen.secret.FAL_KEY -a FAL_KEY -w <key>`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

API_BASE = "https://queue.fal.run"
POLL_S = 3.0

# Cheap-but-good defaults (keep in sync with tools/fal.py + video-gen skill)
DEFAULT_IMAGE = "fal-ai/flux/schnell"
DEFAULT_VIDEO = "fal-ai/kling-video/v1.6/pro"
DEFAULT_AUDIO = "fal-ai/stable-audio"


def resolve_key() -> str:
    v = os.environ.get("FAL_KEY", "").strip()
    if v:
        return v
    # tools-harness/.env
    here = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(os.path.dirname(here), ".env")
    if os.path.isfile(env_file):
        for line in open(env_file, encoding="utf-8"):
            line = line.strip()
            if line.startswith("FAL_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    # macOS Keychain (as written by tools/env_secrets.py)
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "clixen.secret.FAL_KEY", "-w"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            data = json.loads(r.stdout.strip())
            v = (data or {}).get("value", "").strip()
            if v:
                return v
    except Exception:
        pass
    return ""


def request(method: str, url: str, key: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Key {key}")
    req.add_header("Content-Type", "application/json")
    body = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=body, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"_http_error": e.code, "detail": str(e)}
    except Exception as e:
        return {"_error": str(e)}


def poll(key: str, model: str, submit: dict) -> dict:
    rid = submit.get("request_id")
    if not rid:
        return {"_error": submit.get("detail") or submit.get("_error") or f"rejected: {submit}"}
    status_url = submit.get("status_url") or f"{API_BASE}/{model}/requests/{rid}/status"
    resp_url = submit.get("response_url") or f"{API_BASE}/{model}/requests/{rid}"
    status = "IN_QUEUE"
    while status not in ("COMPLETED",):
        time.sleep(POLL_S)
        s = request("GET", status_url, key)
        status = s.get("status", status)
        if status in ("FAILED", "CANCELLED", "COMPLETED"):
            break
    if status != "COMPLETED":
        return {"_error": f"status {status}: {s.get('error') or s.get('error_type') or s}"}
    return request("GET", resp_url, key)


def _pick_media_url(result: dict) -> str | None:
    imgs = result.get("images")
    if imgs and isinstance(imgs, list) and imgs[0].get("url"):
        return imgs[0]["url"]
    for k in ("video", "audio"):
        v = result.get(k)
        if isinstance(v, dict) and v.get("url"):
            return v["url"]
    if result.get("url"):
        return result["url"]
    return None


def download(url: str, out: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    urllib.request.urlretrieve(url, out)


def build_input(args) -> dict:
    inp = {}
    if args.input:
        with open(args.input) as f:
            inp = json.load(f)
    if args.prompt:
        inp["prompt"] = args.prompt
    # fal image-to-video / reference models use image_url (repeatable)
    imgs = [i for i in args.image if i]
    if imgs:
        # most video models take image_url (last wins); multi-ref models take image_url list
        if len(imgs) == 1:
            inp.setdefault("image_url", imgs[0])
        else:
            inp.setdefault("image_url", imgs)
    if args.image_size:
        inp["image_size"] = args.image_size
    if args.duration:
        inp["duration"] = args.duration
    if args.resolution:
        inp["resolution"] = args.resolution
    if args.aspect_ratio:
        inp["aspect_ratio"] = args.aspect_ratio
    if args.num_images is not None:
        inp["num_images"] = args.num_images
    if args.seed is not None:
        inp["seed"] = args.seed
    for kv in args.param or []:
        k, _, v = kv.partition("=")
        if k:
            inp[k] = json.loads(v) if v.lower() in ("true", "false", "null") else v
    return inp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", nargs="?", choices=["image", "video", "audio", "run"])
    ap.add_argument("--model")
    ap.add_argument("--prompt")
    ap.add_argument("--input")
    ap.add_argument("--image", action="append", default=[])
    ap.add_argument("--image-size", dest="image_size")
    ap.add_argument("--duration", type=int)
    ap.add_argument("--resolution")
    ap.add_argument("--aspect-ratio", dest="aspect_ratio")
    ap.add_argument("--num-images", dest="num_images", type=int)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--param", action="append", default=[])
    ap.add_argument("--out", required=True, help="local output path to download to")
    ap.add_argument("--wait", action="store_true", help="block until done (always on; kept for parity)")
    args = ap.parse_args()

    key = resolve_key()
    if not key:
        print("[fal] FAL_KEY not found — set FAL_KEY env, tools-harness/.env, or Keychain "
              "clixen.secret.FAL_KEY", file=sys.stderr)
        return 2

    model = args.model or {
        "image": DEFAULT_IMAGE,
        "video": DEFAULT_VIDEO,
        "audio": DEFAULT_AUDIO,
        "run": None,
    }.get(args.kind)
    if not model:
        print("[fal] --model required for 'run'", file=sys.stderr)
        return 2

    payload = build_input(args)
    submit = request("POST", f"{API_BASE}/{model}", key, payload)
    result = poll(key, model, submit)
    if "_error" in result:
        print(f"[fal] {result['_error']}", file=sys.stderr)
        return 1

    url = _pick_media_url(result)
    if not url:
        print("[fal] no media url in result:", json.dumps(result, ensure_ascii=False)[:800], file=sys.stderr)
        return 1

    download(url, args.out)
    print(f"fal {model} -> {args.out}")
    print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
