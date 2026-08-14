"""Durable, fingerprint-deduplicated document indexing jobs.

The queue is intentionally small: one desktop process can drain it inline,
while SQLite WAL and the claim update make the same interface safe for future
worker processes.
"""

from __future__ import annotations

import time
from pathlib import Path

from store import dbclose
from tools.document_manifest import fingerprint


_DB_PATH = Path(__file__).parent.parent / "store" / "document_index_jobs.db"


def _conn():
    conn = dbclose.connect(str(_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS index_jobs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT NOT NULL, matter TEXT NOT NULL, "
        "path TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', "
        "created_at REAL NOT NULL, claimed_at REAL, completed_at REAL, "
        "UNIQUE(workspace, matter, path, fingerprint))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_index_jobs_ready ON index_jobs(status, created_at)"
    )
    return conn


def enqueue(path: str | Path, workspace: str, matter: str = "default") -> int | None:
    """Queue a file once per content fingerprint; return its job id."""
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        return None
    workspace_path = str(Path(workspace).expanduser().resolve())
    digest = fingerprint(target)
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO index_jobs(workspace, matter, path, fingerprint, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (workspace_path, matter, str(target), digest, time.time()),
        )
        row = conn.execute(
            "SELECT id FROM index_jobs WHERE workspace=? AND matter=? AND path=? AND fingerprint=?",
            (workspace_path, matter, str(target), digest),
        ).fetchone()
    return int(row[0]) if row else None


def claim(workspace: str | None = None, matter: str | None = None) -> dict | None:
    """Atomically claim the oldest queued job for a worker."""
    with _conn() as conn:
        where = ["status = 'queued'"]
        params: list[str] = []
        if workspace is not None:
            where.append("workspace = ?")
            params.append(str(Path(workspace).expanduser().resolve()))
        if matter is not None:
            where.append("matter = ?")
            params.append(matter)
        row = conn.execute(
            "SELECT id, workspace, matter, path, fingerprint FROM index_jobs WHERE "
            + " AND ".join(where) + " ORDER BY created_at, id LIMIT 1",
            params,
        ).fetchone()
        if not row:
            return None
        now = time.time()
        updated = conn.execute(
            "UPDATE index_jobs SET status='processing', claimed_at=? "
            "WHERE id=? AND status='queued'",
            (now, row[0]),
        ).rowcount
        if not updated:
            return None
    return {"id": row[0], "workspace": row[1], "matter": row[2], "path": row[3], "fingerprint": row[4]}


def recover_stale(max_age_seconds: float = 900) -> int:
    """Requeue processing jobs abandoned by a crashed worker."""
    cutoff = time.time() - max(1.0, float(max_age_seconds))
    with _conn() as conn:
        result = conn.execute(
            "UPDATE index_jobs SET status='queued', claimed_at=NULL "
            "WHERE status='processing' AND claimed_at IS NOT NULL AND claimed_at < ?",
            (cutoff,),
        )
        return result.rowcount


def complete(job_id: int, success: bool = True) -> None:
    status = "completed" if success else "failed"
    with _conn() as conn:
        conn.execute(
            "UPDATE index_jobs SET status=?, completed_at=? WHERE id=?",
            (status, time.time(), int(job_id)),
        )


def pending_count(workspace: str | None = None, matter: str | None = None) -> int:
    with _conn() as conn:
        where = ["status IN ('queued', 'processing')"]
        params: list[str] = []
        if workspace is not None:
            where.append("workspace = ?")
            params.append(str(Path(workspace).expanduser().resolve()))
        if matter is not None:
            where.append("matter = ?")
            params.append(matter)
        row = conn.execute("SELECT COUNT(*) FROM index_jobs WHERE " + " AND ".join(where), params).fetchone()
    return int(row[0])


def forget_path(path: str | Path) -> int:
    """Delete queued, processing, and completed jobs for a removed source."""
    target = str(Path(path).expanduser().resolve())
    with _conn() as conn:
        result = conn.execute("DELETE FROM index_jobs WHERE path = ?", (target,))
        return result.rowcount
