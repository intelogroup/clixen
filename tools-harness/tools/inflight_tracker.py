"""
Tracks the in-flight (chat_id, query) pair per channel (telegram, whatsapp, ...).

Native crashes (SIGABRT/SIGSEGV — e.g. from onnxruntime/CoreML) can't be caught
by a Python try/except and kill the process mid-request. Marking the request
before dispatch lets the respawned process notice a stale marker on startup
and tell the user their request was dropped, instead of leaving them silently
unanswered.
"""
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "data" / "inflight"


def _file(channel: str) -> Path:
    return _DIR / f"{channel}.json"


def mark(channel: str, chat_id: str, query: str) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    _file(channel).write_text(json.dumps({"chat_id": chat_id, "query": query}))


def clear(channel: str) -> None:
    _file(channel).unlink(missing_ok=True)


def pop_stale(channel: str) -> dict | None:
    """Return and clear the marker left behind by a crashed run, or None if clean."""
    f = _file(channel)
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
    except Exception:
        data = None
    f.unlink(missing_ok=True)
    return data
