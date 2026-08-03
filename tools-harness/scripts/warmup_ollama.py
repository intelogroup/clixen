#!/usr/bin/env python3
"""Warm Ollama models at startup with keep_alive=-1."""
import urllib.request
import json
import os
import sys

MODELS = [
    os.environ.get("OLLAMA_DEFAULT_MODEL", "gemma4:12b-mlx"),
    os.environ.get("OLLAMA_REWRITE_MODEL", "qwen3.5:4b"),
    "nomic-embed-text",
]

for model in MODELS:
    try:
        body = json.dumps({"model": model, "keep_alive": -1, "messages": []}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
        print(f"warmed {model}", flush=True)
    except Exception as e:
        print(f"warmup failed {model}: {e}", file=sys.stderr, flush=True)
