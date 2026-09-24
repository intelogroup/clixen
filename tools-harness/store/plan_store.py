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
    cols = {r[1] for r in conn.execute("PRAGMA table_info(plans)")}
    if "statuses" not in cols:
        conn.execute("ALTER TABLE plans ADD COLUMN statuses TEXT NOT NULL DEFAULT '[]'")
    return conn


def set_plan(run_id: str, steps: list[str]) -> None:
    """Create/replace the plan for a run. Resets done-markers."""
    with _conn() as conn:
        (seq,) = conn.execute("SELECT COALESCE(MAX(updated_at), 0) + 1 FROM plans").fetchone()
        conn.execute(
            "INSERT INTO plans (run_id, steps, done, statuses, updated_at) VALUES (?, ?, '[]', ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET steps=excluded.steps, done='[]', "
            "statuses=excluded.statuses, updated_at=excluded.updated_at",
            (run_id, json.dumps(steps), json.dumps(["pending"] * len(steps)), seq),
        )
        _evict_if_over_cap(conn)


STEP_STATUSES = ("pending", "in_progress", "done", "blocked")


def set_step_status(run_id: str, index: int, status: str) -> dict | None:
    """Set one step's status (M4: beyond done). Returns the updated plan."""
    if status not in STEP_STATUSES:
        raise ValueError(f"unknown step status: {status!r}")
    with _conn() as conn:
        row = conn.execute(
            "SELECT steps, done, statuses FROM plans WHERE run_id = ?",
            (run_id,)).fetchone()
        if row is None:
            return None
        steps = json.loads(row[0])
        if not 0 <= index < len(steps):
            raise IndexError(f"step {index} out of range ({len(steps)} steps)")
        statuses = json.loads(row[2]) if row[2] else ["pending"] * len(steps)
        while len(statuses) < len(steps):
            statuses.append("pending")
        statuses[index] = status
        done = [i for i, s in enumerate(statuses) if s == "done"]
        conn.execute(
            "UPDATE plans SET statuses = ?, done = ?, updated_at ="
            " (SELECT COALESCE(MAX(updated_at), 0) + 1 FROM plans) WHERE run_id = ?",
            (json.dumps(statuses), json.dumps(done), run_id))
    return get_plan(run_id)


def mark_done(run_id: str, indices: list[int]) -> dict | None:
    """Mark step indices done. Returns the updated plan, or None if no plan exists."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT steps, done, statuses FROM plans WHERE run_id = ?",
            (run_id,)).fetchone()
        if not row:
            return None
        steps = json.loads(row[0])
        done = set(json.loads(row[1]))
        done.update(i for i in indices if 0 <= i < len(steps))
        statuses = json.loads(row[2]) if row[2] else ["pending"] * len(steps)
        while len(statuses) < len(steps):
            statuses.append("pending")
        for i in done:
            statuses[i] = "done"  # keep statuses in sync with the legacy API
        conn.execute(
            "UPDATE plans SET done = ?, statuses = ? WHERE run_id = ?",
            (json.dumps(sorted(done)), json.dumps(statuses), run_id))
        return {"steps": steps, "done": sorted(done), "statuses": statuses}


def get_plan(run_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT steps, done, statuses FROM plans WHERE run_id = ?",
            (run_id,)).fetchone()
    if not row:
        return None
    steps = json.loads(row[0])
    done = json.loads(row[1])
    statuses = json.loads(row[2]) if row[2] else []
    if len(statuses) != len(steps):  # legacy rows: derive from done
        statuses = ["done" if i in set(done) else "pending" for i in range(len(steps))]
    return {"steps": steps, "done": done, "statuses": statuses}


def plan_block(run_id: str) -> str:
    """Render the plan as a system-prompt-injectable block, or '' if none."""
    plan = get_plan(run_id)
    if not plan or not plan["steps"]:
        return ""
    marks = {"done": "x", "in_progress": ">", "blocked": "!", "pending": " "}
    lines = [
        f"  [{marks.get(s, ' ')}] {step}"
        for step, s in zip(plan["steps"], plan.get("statuses") or [])
    ]
    blocked = [step for step, s in zip(plan["steps"], plan.get("statuses") or [])
               if s == "blocked"]
    if blocked:
        lines.append("  Blocked steps needing attention: " + "; ".join(blocked))
    return "Current task plan:\n" + "\n".join(lines)


def _evict_if_over_cap(conn: sqlite3.Connection) -> None:
    (count,) = conn.execute("SELECT COUNT(*) FROM plans").fetchone()
    if count <= _PLAN_MAX_RUNS:
        return
    stale = conn.execute(
        "SELECT run_id FROM plans ORDER BY updated_at LIMIT ?", (count - _PLAN_MAX_RUNS,)
    ).fetchall()
    conn.executemany("DELETE FROM plans WHERE run_id = ?", [(r[0],) for r in stale])
