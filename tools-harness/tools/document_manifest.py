"""Persistent local manifest for incremental document indexing.

Small interface, durable implementation: callers only ask whether a file needs
indexing and mark it complete. Fingerprints are content hashes, so mtime-only
changes do not cause unnecessary extraction or embedding work.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from store import dbclose


_DB_PATH = Path(__file__).parent.parent / "store" / "document_manifest.db"
_CHUNK_SIZE = 1024 * 1024


def _conn():
    conn = dbclose.connect(str(_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS documents ("
        "path TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, indexed_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    if "status" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS document_versions ("
        "path TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'superseded', "
        "indexed_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(path, fingerprint))"
    )
    return conn


def fingerprint(path: str | Path) -> str:
    """Return a stable SHA-256 fingerprint for a local file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def needs_index(path: str | Path) -> bool:
    """Return true when the file is new or its contents changed."""
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        return False
    current = fingerprint(target)
    with _conn() as conn:
        row = conn.execute("SELECT fingerprint, status FROM documents WHERE path = ?", (str(target),)).fetchone()
    return not row or (row[1] == "active" and row[0] != current)


def mark_indexed(path: str | Path) -> None:
    """Record the content version after indexing succeeds."""
    target = Path(path).expanduser().resolve()
    current = fingerprint(target)
    with _conn() as conn:
        conn.execute(
            "UPDATE document_versions SET status='superseded' WHERE path = ? AND fingerprint <> ?",
            (str(target), current),
        )
        conn.execute(
            "INSERT INTO document_versions(path, fingerprint, status) VALUES(?, ?, 'active') "
            "ON CONFLICT(path, fingerprint) DO UPDATE SET status='active', indexed_at=CURRENT_TIMESTAMP",
            (str(target), current),
        )
        conn.execute(
            "INSERT INTO documents(path, fingerprint) VALUES(?, ?) "
            "ON CONFLICT(path) DO UPDATE SET fingerprint=excluded.fingerprint, status='active', indexed_at=CURRENT_TIMESTAMP",
            (str(target), current),
        )


def set_status(path: str | Path, status: str) -> None:
    target = Path(path).expanduser().resolve()
    current = fingerprint(target)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO document_versions(path, fingerprint, status) VALUES(?, ?, ?) "
            "ON CONFLICT(path, fingerprint) DO UPDATE SET status=excluded.status, indexed_at=CURRENT_TIMESTAMP",
            (str(target), current, status),
        )
        conn.execute(
            "INSERT INTO documents(path, fingerprint, status) VALUES(?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET fingerprint=excluded.fingerprint, status=excluded.status, indexed_at=CURRENT_TIMESTAMP",
            (str(target), current, status),
        )


def forget(path: str | Path) -> None:
    target = Path(path).expanduser().resolve()
    with _conn() as conn:
        conn.execute("DELETE FROM documents WHERE path = ?", (str(target),))
        conn.execute("UPDATE document_versions SET status='forgotten' WHERE path = ?", (str(target),))


def purge(path: str | Path) -> None:
    """Erase all manifest records for a source during complete privacy deletion."""
    target = Path(path).expanduser().resolve()
    with _conn() as conn:
        conn.execute("DELETE FROM document_versions WHERE path = ?", (str(target),))
        conn.execute("DELETE FROM documents WHERE path = ?", (str(target),))


def is_quarantined(path: str | Path) -> bool:
    target = Path(path).expanduser().resolve()
    with _conn() as conn:
        row = conn.execute("SELECT status FROM documents WHERE path = ?", (str(target),)).fetchone()
    return bool(row and row[0] == "quarantined")


def is_active_version(path: str | Path) -> bool:
    """Return whether the indexed fingerprint for a path is the active version."""
    target = Path(path).expanduser().resolve()
    current = fingerprint(target) if target.is_file() else None
    with _conn() as conn:
        row = conn.execute(
            "SELECT status FROM document_versions WHERE path = ? AND fingerprint = ?",
            (str(target), current or ""),
        ).fetchone()
        any_version = conn.execute(
            "SELECT 1 FROM document_versions WHERE path = ? LIMIT 1", (str(target),)
        ).fetchone()
    # Legacy/external indexes may predate the manifest. Keep those searchable;
    # once a version is registered, only the active fingerprint is eligible.
    return (row is None and any_version is None) or bool(row and row[0] == "active")


def versions(path: str | Path) -> list[dict[str, str]]:
    """Return immutable version metadata, newest first."""
    target = Path(path).expanduser().resolve()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT fingerprint, status, indexed_at FROM document_versions "
            "WHERE path = ? ORDER BY indexed_at DESC, fingerprint DESC",
            (str(target),),
        ).fetchall()
    return [{"fingerprint": row[0], "status": row[1], "indexed_at": row[2]} for row in rows]


def sync_candidates(paths: list[str | Path]) -> list[Path]:
    """Return only existing files whose content is not recorded as indexed."""
    return [Path(path).expanduser().resolve() for path in paths if needs_index(path)]
