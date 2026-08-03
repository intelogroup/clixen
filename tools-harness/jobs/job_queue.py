"""
SQLite-backed job queue for agent task dispatch.

Two tables:
  jobs           — task execution queue with checkpoint/retry support
  seen_email_ids — idempotency tracker for the email pipeline (replaces seen_ids.json)

Usage:
    from jobs import job_queue

    job_queue.init()
    # job_id = job_queue.enqueue("some_task", {"key": "value"})
    job = job_queue.claim_next()        # marks it running
    job_queue.checkpoint(job["id"], "email_fetched")
    job_queue.mark_done(job["id"])
"""

import sqlite3
import uuid
from store import dbclose
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "jobs.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    task_name    TEXT NOT NULL,
    params_json  TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'queued',
    current_step TEXT,
    retries      INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_email_ids (
    message_id  TEXT PRIMARY KEY,
    seen_at     TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    con = dbclose.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init() -> None:
    """Create tables if they don't exist (idempotent). Resets stale 'running' jobs."""
    with _conn() as con:
        con.executescript(_CREATE)
        con.execute(
            "UPDATE jobs SET status = 'queued' WHERE status = 'running'"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(task_name: str, params: dict | None = None) -> str:
    """
    Add a job to the queue. Returns the job_id.
    Raises ValueError if an identical active job already exists.
    """
    import json as _json

    job_id = uuid.uuid4().hex
    params_json = _json.dumps(params or {}, sort_keys=True)
    now = _now()

    try:
        with _conn() as con:
            con.execute(
                """
                INSERT INTO jobs (id, task_name, params_json, status, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (job_id, task_name, params_json, now, now),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"Duplicate active job for {task_name}: {e}") from e

    return job_id


def claim_next() -> dict | None:
    """
    Atomically claim the oldest queued job, marking it as running.
    Returns the job row as a dict, or None if the queue is empty.
    """
    now = _now()
    with _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        con.execute(
            "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        job = dict(row)
        job["status"] = "running"
        job["updated_at"] = now
        return job


def checkpoint(job_id: str, step: str) -> None:
    """Record the last successfully completed step for a running job."""
    with _conn() as con:
        con.execute(
            "UPDATE jobs SET current_step = ?, updated_at = ? WHERE id = ?",
            (step, _now(), job_id),
        )


def mark_done(job_id: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE jobs SET status = 'done', updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )


def mark_failed(job_id: str, error: str, max_retries: int = 3) -> None:
    """
    Increment retry count. If retries < max_retries, re-queue the job.
    Otherwise mark it permanently failed and send a Telegram alert.
    """
    now = _now()
    job_row = None
    with _conn() as con:
        row = con.execute(
            "SELECT retries, task_name FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return
        retries = row["retries"] + 1
        new_status = "queued" if retries < max_retries else "failed"
        con.execute(
            """
            UPDATE jobs
            SET status = ?, retries = ?, last_error = ?, current_step = NULL, updated_at = ?
            WHERE id = ?
            """,
            (new_status, retries, error[:2000], now, job_id),
        )
        if new_status == "failed":
            job_row = {"id": job_id, "task_name": row["task_name"], "error": error}

    if job_row:
        _alert_failed(job_row)


def _alert_failed(job: dict) -> None:
    """Send a Telegram message when a job exhausts all retries."""
    try:
        from tools.telegram_send import send_telegram
        msg = (
            f"[task_worker] Job failed permanently\n"
            f"task: {job['task_name']}\n"
            f"id: {job['id'][:8]}\n"
            f"error: {job['error'][:300]}"
        )
        send_telegram(msg)
    except Exception:
        pass  # alert failure must never crash the queue


def get_job(job_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs(limit: int = 20, task_names: list[str] | None = None) -> list[dict]:
    query = "SELECT * FROM jobs"
    params: list[object] = []
    if task_names:
        placeholders = ",".join("?" for _ in task_names)
        query += f" WHERE task_name IN ({placeholders})"
        params.extend(task_names)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
        return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Email idempotency (replaces seen_ids.json)
# ---------------------------------------------------------------------------

def load_seen_ids() -> set[str]:
    with _conn() as con:
        rows = con.execute("SELECT message_id FROM seen_email_ids").fetchall()
        return {r["message_id"] for r in rows}


def save_seen_id(message_id: str) -> None:
    now = _now()
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO seen_email_ids (message_id, seen_at) VALUES (?, ?)",
            (message_id, now),
        )
