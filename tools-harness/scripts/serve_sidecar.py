#!/usr/bin/env python3
"""
Sidecar Model Downloader and Server manager for Clixen (Gemma4Llama).
Helps download GGUF models from Hugging Face and serve them via llama.cpp server.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_RESET = "\033[0m"

MODELS_DIR = Path(__file__).parent.parent.parent / "models"

DEFAULT_GGUFS = {
    "gemma-2-9b-it": {
        "repo": "google/gemma-2-9b-it-GGUF",
        "file": "gemma-2-9b-it-Q4_K_M.gguf",
    },
    "qwen2.5-7b-instruct": {
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "file": "qwen2.5-7b-instruct-q4_k_m.gguf",
    },
    "mistral-7b-instruct-v0.3": {
        "repo": "MaziyarPanahi/Mistral-7B-Instruct-v0.3-GGUF",
        "file": "Mistral-7B-Instruct-v0.3.Q4_K_M.gguf",
    }
}

def download_model(name: str) -> None:
    if name not in DEFAULT_GGUFS:
        print(f"{_RED}Unknown model name. Choose from: {', '.join(DEFAULT_GGUFS.keys())}{_RESET}")
        return

    meta = DEFAULT_GGUFS[name]
    repo = meta["repo"]
    filename = meta["file"]
    
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    target_path = MODELS_DIR / filename

    if target_path.exists():
        print(f"{_GREEN}Model already downloaded at: {target_path}{_RESET}")
        return

    print(f"\n{_BLUE}Downloading {filename} from {repo}...{_RESET}")
    
    try:
        from huggingface_hub import hf_hub_download
        print("Using huggingface_hub package...")
        hf_hub_download(
            repo_id=repo,
            filename=filename,
            local_dir=MODELS_DIR,
            local_dir_use_symlinks=False
        )
        print(f"\n{_GREEN}✓ Successfully downloaded to {target_path}{_RESET}")
    except ImportError:
        # Fallback to curl
        print("huggingface-hub not installed. Falling back to curl...")
        url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
        cmd = ["curl", "-L", "-o", str(target_path), url]
        try:
            subprocess.run(cmd, check=True)
            print(f"\n{_GREEN}✓ Successfully downloaded to {target_path}{_RESET}")
        except Exception as e:
            print(f"{_RED}Failed to download using curl: {e}{_RESET}")

def find_llama_server() -> str | None:
    # Check PATH first
    path_bin = shutil.which("llama-server") or shutil.which("llama-cli")
    if path_bin:
        return path_bin

    # Common macOS Homebrew locations
    brew_paths = [
        "/opt/homebrew/bin/llama-server",
        "/usr/local/bin/llama-server",
        "/opt/homebrew/bin/llama-cli",
    ]
    for p in brew_paths:
        if os.path.exists(p):
            return p

    return None

def serve_model(name: str, port: int = 8000, ctx: int = 8192) -> None:
    if name not in DEFAULT_GGUFS:
        print(f"{_RED}Unknown model name.{_RESET}")
        return

    filename = DEFAULT_GGUFS[name]["file"]
    model_path = MODELS_DIR / filename

    if not model_path.exists():
        print(f"{_RED}Model file not found at {model_path}. Download it first!{_RESET}")
        return

    binary = find_llama_server()
    if not binary:
        print(f"{_RED}Error: llama-server (llama.cpp) binary not found on your system.{_RESET}")
        print("Please install llama.cpp:")
        print(f"  macOS: {_YELLOW}brew install llama.cpp{_RESET}")
        print(f"  Linux/Windows: Download build from GitHub or build from source.")
        return

    print(f"\n{_BLUE}Starting llama-server for {name}...{_RESET}")
    print(f"  Model: {model_path}")
    print(f"  URL: http://localhost:{port}")
    print(f"  Context Size: {ctx}")
    
    # Exec llama-server
    cmd = [
        binary,
        "--model", str(model_path),
        "--port", str(port),
        "--ctx-size", str(ctx),
        "--ngl", "-1"  # offload all layers to GPU (Metal/CUDA)
    ]
    
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print(f"\n{_YELLOW}Stopping llama-server...{_RESET}")

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/serve_sidecar.py download <model_name>")
        print("  python scripts/serve_sidecar.py serve <model_name> [port]")
        print("\nAvailable Models:")
        for k, v in DEFAULT_GGUFS.items():
            print(f"  - {k} ({v['file']})")
        return

    action = sys.argv[1]
    if action == "download":
        if len(sys.argv) < 3:
            print("Please specify a model name.")
            return
        download_model(sys.argv[2])
    elif action == "serve":
        if len(sys.argv) < 3:
            print("Please specify a model name.")
            return
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 8000
        serve_model(sys.argv[2], port)
    else:
        print(f"Unknown action: {action}")

if __name__ == "__main__":
    main()
