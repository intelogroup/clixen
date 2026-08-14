"""
Clixen Core — consolidated service entry point.
Runs: chat_ui + kokoro_daemon + voiceprint_daemon in one process.
The task worker runs as its own launchd job (com.clixen.task_worker) so it
survives core.py crashes and can be kickstarted independently.

Run:  python core.py
"""

import sys
import os
import subprocess
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import threading
import time
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from tools.env_secrets import load_secrets

load_secrets()

_LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"


def _run_chat_ui():
    import uvicorn
    from chat_ui import app

    # NOTE: CLIXEN_DEV_MODE (skips auth on /chat/local-agent/*) is intentionally NOT
    # forced here — it used to be auto-enabled, permanently opening full filesystem
    # read/write/exec endpoints to the network. Opt in explicitly:
    #   CLIXEN_DEV_MODE=1 python chat_ui.py
    uvicorn.run(app, host="127.0.0.1", port=9234, log_level="warning")


def _run_telegram_bot():
    from telegram_bot import main as telegram_main

    telegram_main()


def _run_kokoro_daemon():
    from kokoro_daemon import main as _kokoro_main

    _kokoro_main()


def _run_voiceprint_daemon():
    from voiceprint_daemon import main as _voiceprint_main

    _voiceprint_main()


def _surya_python() -> str | None:
    """Resolve Surya's optional isolated runtime without coupling it to OCR-LLM."""
    candidates = []
    configured = os.environ.get("SURYA_PYTHON", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    venv = os.environ.get("SURYA_VENV", "").strip()
    if venv:
        candidates.append(Path(venv).expanduser() / "bin" / "python")
    repo = os.environ.get("SURYA_REPO", "").strip()
    if repo:
        base = Path(repo).expanduser()
        candidates.extend([base / ".venv" / "bin" / "python", base / ".venv-surya" / "bin" / "python"])
    candidates.extend([
        Path.home() / ".surya" / ".venv" / "bin" / "python",
        Path.home() / ".clixen-surya" / ".venv" / "bin" / "python",
    ])
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _run_surya_daemon():
    """Spawn the Surya daemon as its OWN process (the 0.22 VLM needs its own
    venv + llama.cpp backend; a clean machine stays clean when no Surya runtime
    is configured)."""
    import urllib.request

    py = _surya_python()
    daemon = Path(__file__).resolve().parent / "surya_daemon.py"
    url = os.environ.get("SURYA_URL", "http://127.0.0.1:9240").rstrip("/")

    def _alive() -> bool:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    while True:
        if not _alive():
            proc = subprocess.Popen(
                [py, str(daemon)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                proc.wait()
            finally:
                try:
                    proc.terminate()
                except Exception:
                    pass
        time.sleep(30)


def _run_chatterbox_daemon():
    """Spawn the Chatterbox voice-cloning TTS daemon as its OWN process (torch +
    the multilingual model shouldn't live in core.py's process). Same
    health-probe/gate pattern as the other optional local daemons. CHATTERBOX_REFERENCE_WAV
    defaults to the cloned ringback voice sample so French calls use that voice
    without extra setup; override or unset to fall back to the stock model voice."""
    import urllib.request

    daemon = Path(__file__).resolve().parent / "chatterbox_daemon.py"
    url = os.environ.get("CHATTERBOX_TTS_URL_BASE", "http://127.0.0.1:9241").rstrip("/")
    env = os.environ.copy()
    env.setdefault(
        "CHATTERBOX_REFERENCE_WAV",
        str(Path(__file__).resolve().parent
            / "ringback" / "voices" / "-bkiqjkrNRU" / "clip-30s.voice.wav"),
    )

    def _alive() -> bool:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    while True:
        if not _alive():
            proc = subprocess.Popen(
                [sys.executable, str(daemon)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                proc.wait()
            finally:
                try:
                    proc.terminate()
                except Exception:
                    pass
        time.sleep(30)


_TARGETS = {
    "chat_ui": _run_chat_ui,
    "kokoro_daemon": _run_kokoro_daemon,
    "voiceprint_daemon": _run_voiceprint_daemon,
    "chatterbox_daemon": _run_chatterbox_daemon,
}
if (
    _surya_python() is not None
    and (Path(__file__).resolve().parent / "surya_daemon.py").exists()
):
    _TARGETS["surya_daemon"] = _run_surya_daemon
_RESTART_BACKOFF = 30  # seconds between restart attempts
# Crash-loop guard: if a service dies within _CRASH_LOOP_WINDOW_S of its last
# restart more than _CRASH_LOOP_MAX times, stop restarting it and log loudly —
# otherwise a boot-time failure (port conflict, bad import, corrupt DB) restarts
# forever every 30s, spamming logs and churning partial init.
_CRASH_LOOP_WINDOW_S = 120
_CRASH_LOOP_MAX = 5


def _make_thread(name: str) -> threading.Thread:
    return threading.Thread(target=_TARGETS[name], daemon=True, name=name)


def main():
    print("Starting Clixen Core (consolidated)...")

    from tools.connector_ringback import reap_orphaned_processes
    reap_orphaned_processes()

    threads: dict[str, threading.Thread] = {}
    last_restart: dict[str, float] = {}
    restart_counts: dict[str, int] = {}

    for name in _TARGETS:
        t = _make_thread(name)
        threads[name] = t
        print(f"Starting {name}...")
        t.start()
        time.sleep(1)

    print("All services started. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(10)
            now = time.time()
            for name, t in list(threads.items()):
                if not t.is_alive():
                    since_last = now - last_restart.get(name, 0)
                    # Count restarts inside the window; reset when the service
                    # stays up long enough to be considered stable.
                    if since_last > _CRASH_LOOP_WINDOW_S:
                        restart_counts[name] = 0
                    if since_last >= _RESTART_BACKOFF:
                        restart_counts[name] = restart_counts.get(name, 0) + 1
                        if restart_counts[name] > _CRASH_LOOP_MAX:
                            print(
                                f"FATAL: {name} crashed {restart_counts[name]} times within "
                                f"{_CRASH_LOOP_WINDOW_S}s — giving up on auto-restart. "
                                "Check logs and fix the underlying issue."
                            )
                            # Drop it from supervision (daemon thread stays dead).
                            threads.pop(name, None)
                            continue
                        print(f"WARNING: {name} died! Restarting... ({restart_counts[name]}/{_CRASH_LOOP_MAX})")
                        new_t = _make_thread(name)
                        threads[name] = new_t
                        last_restart[name] = now
                        new_t.start()
                    else:
                        print(f"WARNING: {name} died! (restart in {_RESTART_BACKOFF - since_last:.0f}s)")
    except KeyboardInterrupt:
        print("Shutting down...")


if __name__ == "__main__":
    main()


# ===== audio upload endpoint (added by audio migration) =====
def _register_audio_upload_endpoint(app):
    """Attach POST /chat/upload-audio to a FastAPI/Starlette `app`."""
    try:
        from fastapi import UploadFile, File, HTTPException
    except Exception:
        return  # fastapi not present; silently skip

    import os
    import tempfile

    MAX_BYTES = int(os.environ.get("G4L_AUDIO_MAX_BYTES", str(100 * 1024 * 1024)))
    ALLOWED = {
        "audio/mpeg", "audio/mp3",
        "audio/wav", "audio/x-wav", "audio/wave",
        "audio/mp4", "audio/m4a", "audio/x-m4a",
        "audio/ogg", "audio/flac", "audio/webm",
        "application/octet-stream",
    }

    @app.post("/chat/upload-audio")
    async def upload_audio(file: UploadFile = File(...)):
        if file.content_type and file.content_type.lower() not in ALLOWED:
            raise HTTPException(status_code=415, detail=f"Unsupported audio type: {file.content_type}")
        suffix = os.path.splitext(file.filename or "")[1] or ".bin"
        tmp = tempfile.NamedTemporaryFile(prefix="g4l_audio_", suffix=suffix, delete=False)
        try:
            written = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BYTES:
                    raise HTTPException(status_code=413, detail=f"Audio file exceeds {MAX_BYTES} bytes")
                tmp.write(chunk)
            tmp.flush()
            tmp.close()
            try:
                from tools.audio_tools import transcribe_audio
            except Exception:
                from .tools.audio_tools import transcribe_audio  # type: ignore
            result = transcribe_audio(tmp.name)
            return {"ok": True, **result}
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
# ===== end audio upload endpoint =====
