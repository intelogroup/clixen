"""
Clixen health-check CLI.

Usage:
    python -m doctor
    python doctor.py
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

_HERE = Path(__file__).parent
_ENV_FILE = _HERE / ".env"
_LANCEDB_DIR = _HERE / "store" / "lancedb"
_SESSIONS_DIR = _HERE / "store" / "sessions"

_OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
_WARM_MODELS = ["gemma4:12b-mlx", "mistral-nemo"]
_ENV_GROUPS = {
    "Cloud LLM": {
        "keys": ["OPENROUTER_API_KEY"],
        "critical": True,
    },
    "Telegram": {
        "keys": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_CHAT_ID"],
        "critical": True,
    },
    "Web Search": {
        "keys": ["TAVILY_API_KEY", "SEARXNG_URL", "EXA_API_KEY", "BRAVE_SEARCH_API_KEY"],
        "critical": False,
    },
    "STT / TTS": {
        "keys": ["WHISPER_MODEL_PATH", "KOKORO_ONNX_PATH", "KOKORO_VOICES_PATH"],
        "critical": False,
    },
    "WhatsApp": {
        "keys": ["WHATSAPP_BRIDGE_URL"],
        "critical": False,
    },
    "Google API": {
        "keys": ["GOOGLE_CREDENTIALS_PATH", "GOOGLE_TOKEN_PATH"],
        "critical": False,
    },
    "Other": {
        "keys": ["OLLAMA_BASE_URL", "CLOUD_DAILY_TOKEN_BUDGET", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
        "critical": False,
    },
}
_PLISTS = [
    "com.clixen.core",
    "com.clixen.task_worker",
    "com.clixen.messaging",
]

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _ok(label: str, detail: str = "") -> None:
    suffix = f"  {detail}" if detail else ""
    print(f"  {_GREEN}✓{_RESET}  {label}{suffix}")


def _fail(label: str, detail: str = "") -> None:
    suffix = f"  {detail}" if detail else ""
    print(f"  {_RED}✗{_RESET}  {label}{suffix}")


def _warn(label: str, detail: str = "") -> None:
    suffix = f"  {detail}" if detail else ""
    print(f"  {_YELLOW}!{_RESET}  {label}{suffix}")


def check_ollama() -> bool:
    try:
        import requests
        r = requests.get(f"{_OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m["name"].split(":")[0] for m in r.json().get("models", [])]
        _ok("Ollama reachable", f"{len(models)} models loaded")
        return True
    except Exception as e:
        _fail("Ollama unreachable", str(e))
        return False


def check_warm_models() -> None:
    try:
        import requests
        r = requests.get(f"{_OLLAMA_URL}/api/ps", timeout=5)
        r.raise_for_status()
        running = [m["name"].split(":")[0] for m in r.json().get("models", [])]
        for model in _WARM_MODELS:
            if model in running:
                _ok(f"Model warm: {model}")
            else:
                _warn(f"Model not warm: {model}", "will load on first use")
    except Exception as e:
        _warn("Could not check warm models", str(e))


def check_lancedb() -> None:
    if not _LANCEDB_DIR.exists():
        _warn("LanceDB dir missing", str(_LANCEDB_DIR))
        return
    # Check for lockfile
    lock = _LANCEDB_DIR / ".lock"
    if lock.exists():
        _warn("LanceDB lock file exists", "another process may hold the DB")
    else:
        _ok("LanceDB not locked")


def _has_env(key: str) -> bool:
    v = os.environ.get(key, "")
    return bool(v.strip())

def check_env() -> None:
    if not _ENV_FILE.exists():
        _fail(".env file missing", str(_ENV_FILE))
        return
    load_dotenv(_ENV_FILE)
    for group, cfg in _ENV_GROUPS.items():
        label = group if not cfg["critical"] else f"{group} (required)"
        missing = [k for k in cfg["keys"] if not _has_env(k)]
        if not missing:
            _ok(label)
        elif cfg["critical"]:
            _fail(label, f"missing: {', '.join(missing)}")
        else:
            _warn(label, f"missing: {', '.join(missing)}")


def check_plists() -> None:
    for label in _PLISTS:
        try:
            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                _ok(f"launchd service loaded: {label}")
            else:
                _warn(f"launchd service not loaded: {label}", "run: launchctl load ~/Library/LaunchAgents/<name>.plist")
        except Exception as e:
            _warn(f"launchd check failed: {label}", str(e))


def check_sessions_dir() -> None:
    if _SESSIONS_DIR.exists():
        count = len(list(_SESSIONS_DIR.glob("*.json")))
        _ok("Session persistence dir exists", f"{count} sessions on disk")
    else:
        _warn("Session persistence dir missing", "will be created on first chat")


def check_hardware_profile() -> None:
    try:
        from scripts.profile_hardware import get_cpu_info, get_ram_gb, get_gpu_vram_gb, get_recommendation
        cpu = get_cpu_info()
        ram = get_ram_gb()
        gpu, vram = get_gpu_vram_gb()
        rec = get_recommendation(ram, gpu, vram)
        
        _ok(f"CPU: {cpu}")
        _ok(f"RAM: {ram:.1f} GB")
        _ok(f"GPU/VRAM: {gpu} ({vram:.1f} GB)")
        _ok(f"Tier: {rec['tier']}")
        _ok(f"Recommended Model: {rec['primary']}")
    except Exception as e:
        _warn("Hardware check failed", str(e))


def main() -> None:
    print(f"\nClixen Doctor — {_OLLAMA_URL}\n")

    print("[ Ollama ]")
    ollama_ok = check_ollama()
    if ollama_ok:
        check_warm_models()

    print("\n[ Hardware Recommendations ]")
    check_hardware_profile()

    print("\n[ Storage ]")
    check_lancedb()
    check_sessions_dir()

    print("\n[ Environment ]")
    check_env()

    print("\n[ Services ]")
    check_plists()

    print()


if __name__ == "__main__":
    main()
