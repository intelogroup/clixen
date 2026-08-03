#!/usr/bin/env bash
# =============================================================================
# Clixen bootstrap — gets a fresh clone running on any macOS/Linux machine.
#
#   ./setup.sh                # full setup (submodules, venv, deps, models)
#   ./setup.sh --no-models    # skip model downloads (fast, code-only)
#   ./setup.sh --use-uv       # use uv sync instead of venv+pip
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_MODELS=0
USE_UV=0

for arg in "$@"; do
  case "$arg" in
    --no-models) SKIP_MODELS=1 ;;
    --use-uv) USE_UV=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

echo "==> Clixen setup in $REPO_ROOT"

# --- 1. git submodules (ringback SIP voice source) -------------------------
if [ -f "$REPO_ROOT/.gitmodules" ]; then
  echo "==> Initializing git submodules..."
  git -C "$REPO_ROOT" submodule update --init --recursive
fi

# --- 2. Python environment -------------------------------------------------
if [ "$USE_UV" = "1" ]; then
  echo "==> Installing deps with uv (from pyproject.toml)..."
  (cd "$REPO_ROOT" && uv sync --extra test)
  PYTHON="$REPO_ROOT/.venv/bin/python"
else
  echo "==> Creating venv and installing deps..."
  if [ ! -x "$REPO_ROOT/.venv/bin/python" ]; then
    python3 -m venv "$REPO_ROOT/.venv"
  fi
  "$REPO_ROOT/.venv/bin/pip" install --upgrade pip
  "$REPO_ROOT/.venv/bin/pip" install -e "$REPO_ROOT"[test]
  PYTHON="$REPO_ROOT/.venv/bin/python"
fi

# --- 3. Node deps (WhatsApp bridge + google-mcp) ---------------------------
if [ -x "$(command -v node)" ]; then
  echo "==> Installing Node deps (whatsapp bridge + google-mcp)..."
  if [ -f "$REPO_ROOT/tools-harness/package.json" ]; then
    (cd "$REPO_ROOT/tools-harness" && npm install --no-audit --no-fund) || echo "    (tools-harness npm install failed — bridge won't run)"
  fi
  if [ -f "$REPO_ROOT/google-mcp/package.json" ]; then
    (cd "$REPO_ROOT/google-mcp" && npm install --no-audit --no-fund) || echo "    (google-mcp npm install failed)"
  fi
fi

# --- 4. Models --------------------------------------------------------------
if [ "$SKIP_MODELS" = "1" ]; then
  echo "==> Skipping model downloads (--no-models)"
else
  echo "==> Models directory: $REPO_ROOT/models"
  if [ ! -f "$REPO_ROOT/models/kokoro-v1.0.onnx" ]; then
    echo "  Missing Kokoro TTS model. Download from:"
    echo "  https://github.com/thewh1teagle/kokoro-onnx/releases  (kokoro-v1.0.onnx + voices-v1.0.bin)"
  fi
  if [ ! -f "$REPO_ROOT/models/ggml-base.bin" ]; then
    echo "  Missing Whisper model. Pull from HuggingFace (ggerganov/whisper.cpp):"
    echo "  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
  fi
  if [ -x "$(command -v ollama)" ]; then
    echo "==> Pulling default local models (Ollama)..."
    ollama pull gemma4:12b-mlx || ollama pull gemma4:latest || echo "  (ollama pull failed — check Ollama is running; cloud-first works without it)"
    ollama pull qwen3.5:4b || true
    ollama pull nomic-embed-text || true
  else
    echo "  Ollama not found — install from https://ollama.com (the web UI works cloud-first without it)."
  fi
fi

# --- 5. .env bootstrap ------------------------------------------------------
ENV_FILE="$REPO_ROOT/tools-harness/.env"
if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$REPO_ROOT/tools-harness/.env.example" ]; then
    echo "==> Creating tools-harness/.env from .env.example (edit it with your keys)"
    cp "$REPO_ROOT/tools-harness/.env.example" "$ENV_FILE"
  else
    echo "  WARNING: no .env.example found; creating empty .env"
    touch "$ENV_FILE"
  fi
fi

echo ""
echo "==> Done. Next steps:"
echo "  - edit tools-harness/.env with your API keys (see .env.example)"
echo "  - run the health check:   $PYTHON $REPO_ROOT/tools-harness/doctor.py"
echo "  - start the web UI:       cd $REPO_ROOT/tools-harness && $PYTHON chat_ui.py"
echo "  - (optional) install launchd services: see tools-harness/launchd/"
