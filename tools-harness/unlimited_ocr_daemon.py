#!/usr/bin/env python3
"""Warm Unlimited-OCR daemon — a persistent process holding the 6.7B bf16
baidu/Unlimited-OCR VLM on MPS, serving OCR over a tiny local HTTP endpoint.

Why a separate process (not a core.py thread like kokoro_daemon):
  - Unlimited-OCR needs its OWN venv (python3.13 + transformers==4.57.1 +
    torch; the Mac-adapted remote-code model refuses older transformers), which
    is not the clixen venv.
  - A 6.7B model + its MPS working set in core.py's process would crowd the
    24GB Mac alongside ollama/chat_ui. Isolated process = a crash/OOM here never
    takes core down.

Model install (one-time, see the Mac fork README):
  git clone https://github.com/Konsn666/unlimited-ocr-mac.git
  cd unlimited-ocr-mac && bash scripts/install.sh     # downloads 6.7GB, patches
Then point UNLIMITED_OCR_MODEL_DIR at its model_dir (or set UNLIMITED_OCR_REPO).

Endpoints:
  GET  /health        -> "ok"
  POST /ocr           -> {"image_path": str} (optional prompt/max_length/...)
                         -> {"text": "<|det|>...", "elapsed_s": float}
Run as its own core.py-supervised subprocess thread (see core.py), gated on the
model + venv actually being present so a machine without Unlimited-OCR stays clean.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("UNLIMITED_OCR_PORT", "9239"))
BIND_HOST = os.environ.get("UNLIMITED_OCR_BIND_HOST", "127.0.0.1")

log = logging.getLogger("unlimited_ocr_daemon")


def _resolve_model_dir() -> Path:
    env = os.environ.get("UNLIMITED_OCR_MODEL_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    repo = os.environ.get("UNLIMITED_OCR_REPO", "").strip()
    if repo:
        return Path(repo).expanduser() / "model_dir"
    return Path.home() / ".unlimited-ocr" / "model_dir"


def _resolve_auth_token() -> str | None:
    token = os.environ.get("UNLIMITED_OCR_AUTH_TOKEN", "").strip()
    return token or None


def _auth_ok(handler: BaseHTTPRequestHandler) -> bool:
    token = _resolve_auth_token()
    if not token:
        return True
    return handler.headers.get("X-Auth-Token", "") == token


# ---------------------------------------------------------------------------
# Model load + .cuda() -> .to('mps') patching (ported from the Mac fork's
# run_mac.py — official upstream only supports NVIDIA CUDA 12.9+).
# ---------------------------------------------------------------------------

_model = None
_tokenizer = None
_model_lock = threading_lock = __import__("threading").Lock()


def _apply_patches(model_dir: Path, device_str: str) -> Path:
    """Copy model_dir -> <parent>/model_dir_patched, rewriting .cuda() ->
    .to(<device>) and autocast("cuda") so the model runs on MPS. Idempotent."""
    patched = model_dir.parent / "model_dir_patched"
    src_files = ("modeling_unlimitedocr.py", "modeling_deepseekv2.py", "deepencoder.py")
    if patched.exists():
        try:
            src_mtime = max((model_dir / f).stat().st_mtime for f in src_files if (model_dir / f).exists())
            dst_mtime = (patched / "modeling_unlimitedocr.py").stat().st_mtime
            if dst_mtime >= src_mtime:
                return patched
        except OSError:
            pass
        shutil.rmtree(patched)
    shutil.copytree(model_dir, patched)
    for fname in src_files:
        p = patched / fname
        if not p.exists():
            continue
        src = p.read_text()
        orig = src
        src = re.sub(r"\.cuda\(\)", f".to('{device_str}')", src)
        src = re.sub(
            r"\.cuda\(([^)]*)\)",
            lambda m: f".to('{device_str}'" + (f", {m.group(1)}" if m.group(1).strip() else "") + ")",
            src,
        )
        src = re.sub(r'torch\.autocast\("cuda"', f'torch.autocast(device_type="{device_str}"', src)
        src = re.sub(
            r"torch\.get_autocast_gpu_dtype\(\)",
            "torch.get_autocast_dtype(query_states.device.type)",
            src,
        )
        if src != orig:
            p.write_text(src)
            log.info("patched %s for %s", fname, device_str)
    return patched


def _get_model():
    """Lazily load the patched model onto MPS (torch device derived at import)."""
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer
    with _model_lock:
        if _model is not None:
            return _model, _tokenizer
        import torch
        from transformers import AutoModel, AutoTokenizer

        if torch.backends.mps.is_available() and torch.backends.mps.is_built():
            device = torch.device("mps")
            dtype = torch.bfloat16
        else:
            device = torch.device("cpu")
            dtype = torch.float32
            log.warning("MPS unavailable — falling back to CPU (extremely slow)")

        model_dir = _resolve_model_dir()
        if not model_dir.exists():
            raise FileNotFoundError(
                f"Unlimited-OCR model not found at {model_dir}. Set UNLIMITED_OCR_MODEL_DIR "
                "or UNLIMITED_OCR_REPO (see module docstring)."
            )
        patched_dir = _apply_patches(model_dir, str(device))

        t0 = time.time()
        _tokenizer = AutoTokenizer.from_pretrained(str(patched_dir), trust_remote_code=True)
        _model = AutoModel.from_pretrained(
            str(patched_dir),
            trust_remote_code=True,
            use_safetensors=True,
            dtype=dtype,
        ).eval().to(device)
        log.info("model loaded in %.1fs on %s (dtype=%s)", time.time() - t0, device, dtype)
        return _model, _tokenizer


def _run_ocr(
    image_path: str,
    prompt: str = "<image>document parsing.",
    max_length: int = 8192,
    temperature: float = 0.0,
    base_size: int = 1024,
    image_size: int = 640,
    crop_mode: bool = True,
) -> str:
    model, tokenizer = _get_model()
    t0 = time.time()
    text = model.infer(
        tokenizer,
        prompt=prompt,
        image_file=image_path,
        output_path=str(Path(image_path).parent),  # unused in eval_mode; keep valid
        base_size=base_size,
        image_size=image_size,
        crop_mode=crop_mode,
        eval_mode=True,
        max_length=max_length,
        no_repeat_ngram_size=35,
        ngram_window=128,
        temperature=temperature,
        save_results=False,
    )
    log.info("ocr %s in %.1fs", image_path, time.time() - t0)
    return text


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.request.settimeout(600)

    def log_message(self, fmt, *args):  # noqa: A002
        pass

    def _read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return None

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/health":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not _auth_ok(self):
            self._send(401, b"unauthorized", "text/plain")
            return
        if self.path != "/ocr":
            self._send(404, b"not found", "text/plain")
            return
        payload = self._read_json_body()
        if not payload or not payload.get("image_path"):
            self._send(400, b"missing image_path", "text/plain")
            return
        try:
            t0 = time.time()
            text = _run_ocr(
                payload["image_path"],
                prompt=payload.get("prompt", "<image>document parsing."),
                max_length=int(payload.get("max_length", 8192)),
                temperature=float(payload.get("temperature", 0.0)),
                base_size=int(payload.get("base_size", 1024)),
                image_size=int(payload.get("image_size", 640)),
                crop_mode=bool(payload.get("crop_mode", True)),
            )
            self._send(200, json.dumps({"text": text, "elapsed_s": round(time.time() - t0, 2)}).encode())
        except Exception as e:
            log.warning("ocr failed: %s", e, exc_info=True)
            self._send(500, json.dumps({"error": str(e)}).encode())


def main():
    _get_model()  # pay the load cost at startup, not on the first real request
    server = ThreadingHTTPServer((BIND_HOST, PORT), _Handler)
    log.info("Unlimited-OCR daemon listening on %s:%d", BIND_HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    main()
