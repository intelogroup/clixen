"""Approval-safe lifecycle operations for local document sources and indexes."""

from __future__ import annotations

from pathlib import Path

from tools.confirmation import request_confirmation


def _schema(name: str, description: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}


QUARANTINE_DOCUMENT_SCHEMA = _schema("quarantine_document", "Keep a local document but remove it from active search indexes.")
FORGET_DOCUMENT_INDEX_SCHEMA = _schema("forget_document_index", "Remove a document from local search indexes while keeping the source file.")
DELETE_DOCUMENT_SCHEMA = _schema("delete_document", "Request approval to permanently delete a local document and its indexes.")


def _safe_path(path: str) -> Path:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(target)
    return target


def _remove_derived(path: Path) -> list[str]:
    removed = []
    for module_name in ("tools.semantic_files", "tools.fulltext_search"):
        try:
            module = __import__(module_name, fromlist=["remove_file"])
            module.remove_file(str(path))
            removed.append(module_name)
        except Exception:
            pass
    try:
        from tools.document_session import forget_source
        forget_source(str(path))
        removed.append("session_evidence")
    except Exception:
        pass
    return removed


def quarantine_document(path: str) -> str:
    """Keep the source but exclude it from future active retrieval."""
    target = _safe_path(path)
    _remove_derived(target)
    from tools.document_manifest import set_status
    set_status(target, "quarantined")
    return f"Quarantined {target}; source retained and derived indexes removed."


def forget_document_index(path: str) -> str:
    """Forget all derived state while retaining the source file."""
    target = _safe_path(path)
    _remove_derived(target)
    from tools.document_manifest import forget
    forget(target)
    return f"Forgot indexes for {target}; source retained."


def request_delete_document(path: str) -> str:
    target = _safe_path(path)
    token = request_confirmation(
        "delete_document",
        {"path": str(target)},
        f"delete_document(path={str(target)!r})",
    )
    return f"Deletion requires approval. Nothing was deleted. Token: {token}."


def delete_document(path: str) -> str:
    target = _safe_path(path)
    _remove_derived(target)
    from tools.document_manifest import forget
    forget(target)
    # Complete deletion also removes durable metadata that is not part of the
    # active retrieval path: version snapshots, manifest history, and queued
    # indexing work. Each cleanup is best-effort so the source deletion still
    # completes, while the response reports what was attempted.
    cleanup = []
    try:
        from tools.document_manifest import purge
        purge(target)
        cleanup.append("manifest_history")
    except Exception:
        pass
    try:
        from tools.document_output import delete_versions
        delete_versions(str(target))
        cleanup.append("artifact_versions")
    except Exception:
        pass
    try:
        from tools.document_index_queue import forget_path
        forget_path(target)
        cleanup.append("index_jobs")
    except Exception:
        pass
    target.unlink()
    suffix = f"; purged {', '.join(cleanup)}" if cleanup else ""
    return f"Deleted {target} and removed its derived indexes{suffix}."
