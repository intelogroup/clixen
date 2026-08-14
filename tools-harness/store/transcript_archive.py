"""Encrypted, compressed archive for expired raw conversations."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
from pathlib import Path


_ARCHIVE_DIR = Path(__file__).parent / "session_archive"
_KEY_SERVICE = "com.clixen.transcript-archive"


def _get_key() -> bytes:
    """Read the archive key from Keychain; env override is for isolated tests."""
    configured = os.environ.get("CLIXEN_ARCHIVE_KEY", "").strip()
    if configured:
        return configured.encode("ascii")
    if os.name != "posix" or not Path("/usr/bin/security").exists():
        raise RuntimeError("transcript archive keychain unavailable")
    result = subprocess.run(
        ["security", "find-generic-password", "-s", _KEY_SERVICE, "-w"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        saved = subprocess.run(
            ["security", "add-generic-password", "-s", _KEY_SERVICE, "-a", _KEY_SERVICE,
             "-w", key.decode("ascii")], capture_output=True, text=True, check=False,
        )
        if saved.returncode != 0:
            raise RuntimeError("could not store transcript archive key in Keychain")
        return key
    return result.stdout.strip().encode("ascii")


def archive_raw_session(chat_id: str, history: list[dict]) -> Path:
    """Encrypt and gzip a raw transcript before its active JSON is removed."""
    from cryptography.fernet import Fernet

    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"chat_id": str(chat_id), "history": history}, ensure_ascii=False).encode("utf-8")
    encrypted = Fernet(_get_key()).encrypt(gzip.compress(payload))
    safe = hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()
    target = _ARCHIVE_DIR / f"{safe}.json.gz.enc"
    temp = target.with_suffix(".tmp")
    temp.write_bytes(encrypted)
    temp.replace(target)
    return target
