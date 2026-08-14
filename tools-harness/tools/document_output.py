"""Post-export validation for reviewable document outputs."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import shutil
import tempfile
import time

from tools.vault_paths import data_dir


RESTORE_VERSION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "restore_document_version",
        "description": "Restore a prior private artifact version after user approval; the current file is snapshotted first.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Current artifact path"},
                "backup": {"type": "string", "description": "Snapshot path returned by artifact version history"},
            },
            "required": ["path", "backup"],
        },
    },
}


def _version_root() -> Path:
    root = data_dir() / "artifact-versions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def archive_existing(path: str) -> str | None:
    """Snapshot an existing output before an approved overwrite.

    Versions live in Clixen's private app-data directory, not beside the user's
    document. The returned path is suitable for an audit trail or restore UI.
    """
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        return None
    key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:32]
    folder = _version_root() / key
    folder.mkdir(parents=True, exist_ok=True)
    stamp = f"{time.time_ns()}"
    backup = folder / f"{stamp}-{source.name}"
    shutil.copy2(source, backup)
    metadata = folder / f"{stamp}.json"
    metadata.write_text(json.dumps({
        "source": str(source), "backup": str(backup),
        "created_at": time.time(), "bytes": source.stat().st_size,
    }, ensure_ascii=False), encoding="utf-8")
    return str(backup)


def list_versions(path: str, limit: int = 20) -> list[dict]:
    """List private snapshots for a user-visible version history."""
    source = Path(path).expanduser().resolve()
    key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:32]
    folder = _version_root() / key
    rows = []
    for metadata in sorted(folder.glob("*.json"), reverse=True)[:max(1, min(int(limit), 100))]:
        try:
            rows.append(json.loads(metadata.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return rows


def delete_versions(path: str) -> int:
    """Remove all private artifact snapshots associated with a source path."""
    source = Path(path).expanduser().resolve()
    key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:32]
    folder = _version_root() / key
    if not folder.is_dir():
        return 0
    removed = sum(1 for item in folder.iterdir() if item.is_file())
    shutil.rmtree(folder)
    return removed


def restore_version(path: str, backup: str) -> dict:
    """Restore a registered private snapshot, archiving the current file first."""
    source = Path(path).expanduser().resolve()
    requested = Path(backup).expanduser().resolve()
    match = next(
        (row for row in list_versions(str(source), limit=100)
         if Path(row.get("backup", "")).expanduser().resolve() == requested),
        None,
    )
    if match is None:
        return {"ok": False, "path": str(source), "error": "snapshot is not registered for this file"}
    if Path(match.get("source", "")).expanduser().resolve() != source:
        return {"ok": False, "path": str(source), "error": "snapshot does not belong to this file"}
    if not requested.is_file():
        return {"ok": False, "path": str(source), "error": "snapshot file is missing"}
    source.parent.mkdir(parents=True, exist_ok=True)
    archived_current = archive_existing(str(source))
    fd, temp_name = tempfile.mkstemp(prefix=f".{source.name}.restore-", dir=str(source.parent))
    try:
        with os.fdopen(fd, "wb") as handle, requested.open("rb") as snapshot:
            shutil.copyfileobj(snapshot, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, source)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return {
        "ok": True,
        "path": str(source),
        "restored_from": str(requested),
        "archived_current": archived_current,
    }


def verify_export(path: str) -> dict:
    output = Path(path).expanduser()
    if not output.is_file():
        return {"ok": False, "path": str(output), "error": "output file missing"}
    size = output.stat().st_size
    if size == 0:
        return {"ok": False, "path": str(output), "error": "output file is empty"}
    suffix = output.suffix.lower().lstrip(".") or "unknown"
    if suffix == "pdf":
        header = output.read_bytes()[:5]
        if header != b"%PDF-":
            return {"ok": False, "path": str(output), "error": "invalid PDF header"}
    elif suffix in {"docx", "xlsx", "pptx"}:
        import zipfile
        if not zipfile.is_zipfile(output):
            return {"ok": False, "path": str(output), "error": f"invalid {suffix} package"}
    return {"ok": True, "path": str(output), "format": suffix, "bytes": size}
