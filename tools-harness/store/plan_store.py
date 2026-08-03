"""
Per-run mid-task plan/todo state, keyed by run_id — mirrors trace_store's
sqlite pattern. Lets the orchestrator externalize "what's done, what's left"
across tool rounds instead of only implicit in tool-call sequencing, closing
the gap that lets long runs drift off the original ask before verify-on-absence
or claim-check ever get a chance to catch it.
"""
import json
import sqlite3
from store import dbclose
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "data" / "plan_store.sqlite"
_PLAN_MAX_RUNS = 200


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = dbclose.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS plans ("
        "run_id TEXT PRIMARY KEY, steps TEXT NOT NULL, done TEXT NOT NULL, "
        "updated_at INTEGER NOT NULL)"
    )
    return conn


def set_plan(run_id: str, steps: list[str]) -> None:
    """Create/replace the plan for a run. Resets done-markers."""
    with _conn() as conn:
        (seq,) = conn.execute("SELECT COALESCE(MAX(updated_at), 0) + 1 FROM plans").fetchone()
        conn.execute(
            "INSERT INTO plans (run_id, steps, done, updated_at) VALUES (?, ?, '[]', ?) "
            "ON CONFLICT(run_id) DO UPDATE SET steps=excluded.steps, done='[]', updated_at=excluded.updated_at",
            (run_id, json.dumps(steps), seq),
        )
        _evict_if_over_cap(conn)


def mark_done(run_id: str, indices: list[int]) -> dict | None:
    """Mark step indices done. Returns the updated plan, or None if no plan exists."""
    with _conn() as conn:
        row = conn.execute("SELECT steps, done FROM plans WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        steps = json.loads(row[0])
        done = set(json.loads(row[1]))
        done.update(i for i in indices if 0 <= i < len(steps))
        conn.execute("UPDATE plans SET done = ? WHERE run_id = ?", (json.dumps(sorted(done)), run_id))
        return {"steps": steps, "done": sorted(done)}


def get_plan(run_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT steps, done FROM plans WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        return None
    return {"steps": json.loads(row[0]), "done": json.loads(row[1])}


def plan_block(run_id: str) -> str:
    """Render the plan as a system-prompt-injectable block, or '' if none."""
    plan = get_plan(run_id)
    if not plan or not plan["steps"]:
        return ""
    done = set(plan["done"])
    lines = [
        f"  [{'x' if i in done else ' '}] {step}"
        for i, step in enumerate(plan["steps"])
    ]
    return "Current task plan:\n" + "\n".join(lines)


def _evict_if_over_cap(conn: sqlite3.Connection) -> None:
    (count,) = conn.execute("SELECT COUNT(*) FROM plans").fetchone()
    if count <= _PLAN_MAX_RUNS:
        return
    stale = conn.execute(
        "SELECT run_id FROM plans ORDER BY updated_at LIMIT ?", (count - _PLAN_MAX_RUNS,)
    ).fetchall()
    conn.executemany("DELETE FROM plans WHERE run_id = ?", [(r[0],) for r in stale])
