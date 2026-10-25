"""The durable-step seam: one leg of a run, recorded so a crash is recoverable.

Ported from the cloud repo's ADR 0019 ("name the seam now; adopt no engine"). The
shape it preserves, and why each part matters:

- A *step* is one leg of a run, keyed by (run_id, step_index). Not a round, not a
  tool call: the unit a resume needs.
- Data crosses the seam as **plain JSON dicts** — the shape a durable-execution
  engine would checkpoint — never agent-internal objects. Swapping in an engine
  later must be a bounded change, not an excavation.
- Writes are **first-writer-wins**. A racing duplicate loses; the stored step is
  canonical. That is what makes "apply the effect once" possible.

What this module deliberately does NOT do: resume anything. The companion rule is
ADR 0020 — the reconciler stops, never starts. A crashed run leaves a visible,
partially-filled journal; restarting it is an explicit act by the user or the task
path, never a background process guessing.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from store import dbclose

_DB_PATH = Path(
    os.environ.get("CLIXEN_STEP_JOURNAL")
    or Path(__file__).resolve().parent.parent / "data" / "step_journal.sqlite"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS steps (
    run_id      TEXT NOT NULL,
    step_index  INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    outcome     TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    PRIMARY KEY (run_id, step_index)
)
"""


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = dbclose.connect(str(_DB_PATH))
    conn.execute(_SCHEMA)
    return conn


def record(
    run_id: str,
    step_index: int,
    kind: str,
    payload: dict | None = None,
    outcome: str = "",
) -> bool:
    """Record one step. Returns True when this writer won, False on a duplicate.

    `payload` must be JSON-serialisable and shallow — a dict of primitives. An
    object that only exists in memory would make the record unreadable at resume
    time, which is the one thing the seam has to guarantee.
    """
    if not run_id:
        raise ValueError("record() needs a run_id; an unkeyed step cannot be resumed")
    payload = payload or {}
    try:
        encoded = json.dumps(payload, sort_keys=True, default=_no_default)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"step payload is not JSON-serialisable: {exc}") from exc
    with _conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO steps (run_id, step_index, kind, payload, outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, int(step_index), kind, encoded, outcome, time.time()),
        )
        return cur.rowcount == 1


def _no_default(obj):  # never silently stringify an object into the journal
    raise TypeError(f"{type(obj).__name__} is not JSON data — pass primitives only")


def next_index(run_id: str) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(step_index), -1) FROM steps WHERE run_id = ?", (run_id,)
        ).fetchone()
    return int(row[0]) + 1


def record_next(run_id: str, kind: str, payload: dict | None = None, outcome: str = "") -> int:
    """Append a step at the next free index. Single-writer per run by convention."""
    index = next_index(run_id)
    record(run_id, index, kind, payload, outcome)
    return index


def load(run_id: str) -> list[dict]:
    """Every step of a run, in order — the replay door."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT step_index, kind, payload, outcome, created_at FROM steps "
            "WHERE run_id = ? ORDER BY step_index",
            (run_id,),
        ).fetchall()
    return [
        {
            "step_index": r[0],
            "kind": r[1],
            "payload": json.loads(r[2]),
            "outcome": r[3],
            "created_at": r[4],
        }
        for r in rows
    ]


def run_ids(limit: int = 20) -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT run_id, MAX(created_at) AS last FROM steps GROUP BY run_id "
            "ORDER BY last DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [r[0] for r in rows]
