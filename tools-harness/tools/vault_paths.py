"""Vault paths — canonical home for clixen user data.

Ship-killer guard (see THREAT_MODEL.md): clixen data must NEVER live in
~/Documents, ~/Desktop, or ~/Downloads. macOS iCloud syncs Desktop + Documents
by default (every file goes to Apple's cloud), TCC grants for them are
all-or-nothing (any app with the grant sees the whole tree), and Spotlight
indexes them raw. The canonical home is the OS app-private data dir:

    Mac:    ~/Library/Application Support/Clixen/        (not iCloud-synced)
    Windows:%APPDATA%/com.clixen.app/                    (not OneDrive by default)
    Linux:  ~/.local/share/com.clixen.app/               (XDG)

Everything user-owned (knowledge DB, memory, file index, raw docs) resolves
through `data_dir()` here. App telemetry sqlite (event_log, trace_store,
plan_store) intentionally stays repo-local — it's operational, not user data,
and moving live WAL files under a running daemon risks corruption.

Also sets the macOS iCloud/backup-exclusion xattr
(com.apple.metadata:com_apple_backup_excludeItem = binary-plist true) on the
vault dir, so even if the user's machine is backing up Application Support,
clixen data is excluded. Uses the `xattr` CLI with the exact byte recipe Swift
writes (verified read-back: kMDItemFSIsExcludedFromBackup == true) because
this python build lacks os.setxattr.
"""
from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("vault_paths")

# Env override for tests / nonstandard installs.
_CLIXEN_DATA_DIR_ENV = "CLIXEN_DATA_DIR"


def data_dir() -> Path:
    """Canonical app-private data root. Overridable via CLIXEN_DATA_DIR."""
    override = os.environ.get(_CLIXEN_DATA_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Clixen"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "com.clixen.app"
    return Path.home() / ".local/share/com.clixen.app"


def vault_dir() -> Path:
    """User-document vault root (inside data_dir)."""
    return data_dir() / "vault"


def ensure_data_dir() -> Path:
    """Create the data dir (+ vault subdir), apply iCloud-exclusion xattr."""
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "vault").mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        _exclude_from_backup(root)
    return root


def exclude_from_backup(path: Path | str) -> bool:
    """Set macOS com_apple_backup_excludeItem on a dir. Returns True if applied."""
    return _exclude_from_backup(Path(path))


def _exclude_from_backup(path: Path) -> bool:
    if sys.platform != "darwin":
        return False
    if shutil.which("xattr") is None:
        return False
    try:
        blob = plistlib.dumps(True, fmt=plistlib.FMT_BINARY)
        subprocess.run(
            ["xattr", "-wx", "com.apple.metadata:com_apple_backup_excludeItem", blob.hex(), str(path)],
            check=False, capture_output=True, timeout=5,
        )
        # Verify via read-back of the raw xattr.
        probe = subprocess.run(
            ["xattr", "-lx", "com.apple.metadata:com_apple_backup_excludeItem", str(path)],
            check=False, capture_output=True, timeout=5,
        )
        return b"bplist00" in (probe.stdout or b"")
    except Exception as exc:
        log.warning("failed to set iCloud-exclusion xattr on %s: %s", path, exc)
        return False


# ---------------------------------------------------------------------------
# Legacy migration — one-time copy of user knowledge data out of the repo dir
# (tools-harness/data) into the vault dir. Copy, not move: a running daemon
# may hold open handles on the old paths; the repo copies are cleaned up later.
# ---------------------------------------------------------------------------

_LEGACY_ITEMS = (
    "knowledge.lance",
    "memory.lance",
    "file_index.lance",
    "raw.db",
    "science_scout.lance",
    "reddit_intel.lance",
)


def migrate_legacy_data(repo_data_dir: Path | None = None) -> list[str]:
    """Copy known user-data items from the repo data dir into the vault.

    Idempotent: skips any item already present in the vault. Returns the list
    of items migrated.
    """
    if repo_data_dir is None:
        # tools/vault_paths.py → tools-harness/data
        repo_data_dir = Path(__file__).resolve().parent.parent / "data"
    if not repo_data_dir.is_dir():
        return []

    dest = data_dir()
    dest.mkdir(parents=True, exist_ok=True)
    migrated: list[str] = []
    for name in _LEGACY_ITEMS:
        src = repo_data_dir / name
        target = dest / name
        if not src.exists() or target.exists():
            continue
        try:
            if src.is_dir():
                shutil.copytree(src, target)
            else:
                shutil.copy2(src, target)
            migrated.append(name)
        except Exception as exc:
            log.warning("legacy migration failed for %s: %s", src, exc)
    if migrated:
        log.info("vault_paths: migrated legacy data into %s: %s", dest, migrated)
    return migrated


def db_path(name: str) -> str:
    """Resolve a store path under the data dir (e.g. 'knowledge.lance')."""
    return str(data_dir() / name)


if __name__ == "__main__":
    ensure_data_dir()
    print(f"data_dir = {data_dir()}")
    print(f"vault_dir = {vault_dir()}")
    print(f"excluded_from_backup = {exclude_from_backup(data_dir())}")
