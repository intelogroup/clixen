"""Isolated scratch workspace and bounded working notes for document runs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from tools.path_policy import CLIXEN_DATA_DIR

_ROOT = CLIXEN_DATA_DIR / "document-scratch"
_MAX_NOTE_CHARS = 1200
_MAX_NOTES = 32
_TTL_SECONDS = 24 * 3600


def _key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def session_dir(session_id: str, workspace: str | None = None) -> Path:
    """Return a private, deterministic scratch directory for one session."""
    identity = f"{workspace or 'default'}\0{session_id or 'anonymous'}"
    if workspace:
        workspace_root = Path(workspace).expanduser().resolve()
        if not workspace_root.is_dir():
            raise NotADirectoryError(f"workspace is not a directory: {workspace_root}")
        root = workspace_root / ".clixen-scratch"
    else:
        root = _ROOT
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    path = root / _key(identity)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def safe_artifact_path(session_id: str, name: str, workspace: str | None = None) -> Path:
    """Resolve an artifact name strictly below the session scratch directory."""
    root = session_dir(session_id, workspace).resolve()
    candidate = (root / name).resolve()
    if candidate != root and root not in candidate.parents:
        raise PermissionError("scratch artifact must stay inside the session directory")
    if candidate.name in {"", ".", ".."}:
        raise ValueError("invalid scratch artifact name")
    return candidate


def add_note(session_id: str, note: str, workspace: str | None = None) -> str:
    """Append one bounded working note; notes are not hidden model reasoning."""
    if not session_id:
        return "[error] scratchpad requires a session id"
    value = str(note).strip()
    if not value:
        return "[error] scratchpad note is empty"
    path = session_dir(session_id, workspace) / "scratchpad.json"
    notes: list[dict[str, object]] = []
    if path.exists():
        try:
            notes = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            notes = []
    now = time.time()
    notes = [n for n in notes if isinstance(n, dict) and now - float(n.get("created_at", now)) <= _TTL_SECONDS]
    notes.append({"note": value[:_MAX_NOTE_CHARS], "created_at": now})
    notes = notes[-_MAX_NOTES:]
    path.write_text(json.dumps(notes, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, 0o600)
    return f"Scratchpad updated: {len(notes)} note(s)."


def read_notes(session_id: str, workspace: str | None = None) -> list[str]:
    path = session_dir(session_id, workspace) / "scratchpad.json"
    if not path.exists():
        return []
    try:
        now = time.time()
        rows = json.loads(path.read_text(encoding="utf-8"))
        return [str(row["note"]) for row in rows if now - float(row.get("created_at", now)) <= _TTL_SECONDS]
    except (OSError, ValueError, TypeError, KeyError):
        return []


def scratchpad_block(session_id: str | None, workspace: str | None = None) -> str:
    notes = read_notes(session_id, workspace) if session_id else []
    if not notes:
        return ""
    return "Working notes (user-visible state, not hidden reasoning):\n" + "\n".join(f"- {note}" for note in notes)


def cleanup_expired(max_age_seconds: int = _TTL_SECONDS) -> int:
    """Remove only Clixen-owned expired session directories."""
    if not _ROOT.exists():
        return 0
    removed = 0
    cutoff = time.time() - max(1, int(max_age_seconds))
    for child in _ROOT.iterdir():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child)
            removed += 1
    return removed
