"""
Durable run journal — the state core of long-horizon agent runs (M1).

A run is an append-only sequence of events keyed by (run_id, seq). The tool
loop treats the journal as the source of truth:

    load events → one model round → append → repeat      (stateless loop)

Contract (docs/plans/2026-09-24-long-horizon-agent-upgrade.md):
  * Single writer per DB: the child process that owns the run performs all
    appends for it; supervisor/SSE readers only read (WAL + busy_timeout are
    set by dbclose.connect).
  * Write-boundary redaction: every payload is scrubbed of secrets BEFORE it
    touches disk — keyname regex + exact vault values. The journal never
    stores a replayable secret; redacted side-effecting tools are re-executed
    on resume rather than replayed.
  * Status transitions are validated; every failure state preserves the
    journal (a failed run is never a dead end).
  * seq assignment is atomic (INSERT...SELECT) — no MAX+1 read-modify-write
    PK races between threads.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

from store import dbclose

# CLIXEN_RUN_STORE relocates the journal (child processes and tests must share
# one file; the supervisor passes it through the child env).
_DB_PATH = Path(os.environ.get("CLIXEN_RUN_STORE")
                or Path(__file__).parent.parent / "data" / "run_store.sqlite")

# Retention (plan performance budget: 500 runs / 30 days). Overridable in
# tests; prune_runs enforces it on create.
_MAX_RUNS = 500
_MAX_AGE_S = 30 * 24 * 3600

# Journal schema version — bumped when event semantics change so an old
# journal is quarantined rather than silently mis-replayed (plan failure-modes
# table: schema drift ⇒ quarantine + explicit restart, never silent replay).
SCHEMA_VERSION = 1
_DEFAULT_MAX_ATTEMPTS = 3

# Lifecycle states from the plan's Interaction State Table.
STATUSES = (
    "queued", "running", "paused", "steer-waiting", "retrying",
    "succeeded", "failed", "killed", "budget-exceeded",
)
TERMINAL = ("succeeded", "failed", "killed", "budget-exceeded")
# Everything not-terminal is resumable: a paused/steer-waiting run resumes
# explicitly (the plan's state table shows a resume affordance for them).
_RESUMABLE = ("paused", "steer-waiting", "retrying", "failed", "killed",
              "budget-exceeded")

_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "killed", "failed"},
    "running": {"paused", "steer-waiting", "retrying",
                "succeeded", "failed", "killed", "budget-exceeded"},
    "retrying": {"running", "failed", "killed"},
    "paused": {"running", "killed", "failed"},
    "steer-waiting": {"running", "paused", "killed", "failed"},
    # Resume: journal replay starts a fresh executing state.
    "failed": {"running"}, "killed": {"running"}, "budget-exceeded": {"running"},
    "succeeded": set(),  # terminal — rerun means a new run_id
}

# Journal event kinds. Appending an unknown kind is allowed but logged as a
# forward-compatible "custom:" event kind is NOT — keep the vocabulary closed.
EVENT_KINDS = frozenset({
    "run", "user_msg", "assistant_msg", "tool_call", "tool_result",
    "plan_step", "heartbeat", "budget", "steer", "approval", "denial", "status",
    "round", "control",
})

# Control intents the loop understands (M2). The requester only journals
# intent; the executing loop owns the status transition.
CONTROL_ACTIONS = ("pause", "resume", "kill", "steer")

# ── Write-boundary redaction ──────────────────────────────────────────────
_SECRET_KEY_RE = re.compile(
    r"password|passwd|secret|token|authorization|api[_-]?key|credential|otp|pin\b",
    re.IGNORECASE,
)
_vault_cache: dict = {"values": frozenset(), "ts": 0.0}
_VAULT_TTL_S = 300.0


def _vault_secret_values() -> frozenset[str]:
    """Exact values of every string stored in the Keychain vault, cached 5 min.
    Lazy import + broad except: journal appends must never fail because the
    Keychain is unavailable (scrubbing degrades to keyname-only)."""
    now = time.monotonic()
    if now - _vault_cache["ts"] < _VAULT_TTL_S:
        return _vault_cache["values"]
    values: set[str] = set()
    try:
        from tools.vault import _kc_get, _kc_list
        for service in _kc_list():
            data = _kc_get(service) or {}
            for v in data.values():
                s = str(v)
                if len(s) >= 6:  # never match short/generic strings
                    values.add(s)
    except Exception:
        pass  # degrade to last-known cache + keyname regex
    _vault_cache["values"] = frozenset(values)
    _vault_cache["ts"] = now
    return _vault_cache["values"]


def scrub(obj, secrets: frozenset[str] | None = None):
    """Recursively redact secrets. Returns (scrubbed, redacted_keys).

    Two layers, matching the plan:
      1. keyname regex  → dict values under password/token/... keys become
         [REDACTED:<key>] (type preserved for booleans/ints: replaced anyway —
         the journal must not store them).
      2. exact vault values → any string containing a known secret has the
         secret replaced with [REDACTED:vault].
    """
    redacted: list[str] = []
    if secrets is None:
        secrets = _vault_secret_values()

    def _walk(node, path: str):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                    out[k] = f"[REDACTED:{k}]"
                    redacted.append(f"{path}.{k}" if path else str(k))
                else:
                    out[k] = _walk(v, f"{path}.{k}" if path else str(k))
            return out
        if isinstance(node, list):
            return [_walk(v, path) for v in node]
        if isinstance(node, str):
            for s in secrets:
                if s and s in node:
                    redacted.append(f"{path}(vault)")
                    node = node.replace(s, "[REDACTED:vault]")
            return node
        return node

    return _walk(obj, ""), sorted(set(redacted))


# ── Persistence ───────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = dbclose.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runs ("
        "run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
        "trigger TEXT NOT NULL, goal TEXT NOT NULL, policy TEXT NOT NULL,"
        "status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0, last_seq INTEGER NOT NULL DEFAULT 0, "
        "pid INTEGER, lease_expires_at REAL NOT NULL DEFAULT 0)"
    )
    # Additive migrations — DBs created before M3 lack these columns.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    for col, ddl in (("pid", "ALTER TABLE runs ADD COLUMN pid INTEGER"),
                     ("lease_expires_at",
                      "ALTER TABLE runs ADD COLUMN lease_expires_at REAL NOT NULL DEFAULT 0")):
        if col not in cols:
            conn.execute(ddl)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dead_letters ("
        "run_id TEXT PRIMARY KEY, reason TEXT NOT NULL, at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS run_events ("
        "run_id TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT NOT NULL,"
        "kind TEXT NOT NULL, payload TEXT NOT NULL,"
        "PRIMARY KEY (run_id, seq))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_run_events_kind ON run_events (run_id, kind, seq)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_runs_lease ON runs(status, lease_expires_at)"
    )
    return conn


def schema_version() -> int:
    """Persisted journal schema version (SCHEMA_VERSION above).

    A journal written by a DIFFERENT version is quarantined on resume rather
    than silently mis-replayed (plan failure-modes: schema drift ⇒
    quarantine + explicit restart).
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row else SCHEMA_VERSION


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def create_run(goal: str, policy: dict | None = None,
               trigger: str = "chat-send") -> str:
    """Create a queued run + its anchor `run` event. Returns run_id."""
    if trigger not in ("chat-send", "automation-enqueue", "agent-mail"):
        raise ValueError(f"unknown trigger: {trigger!r}")
    run_id = uuid.uuid4().hex[:12]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, created_at, updated_at, trigger, goal, policy, status)"
            " VALUES (?, ?, ?, ?, ?, ?, 'queued')",
            (run_id, _now(), _now(), trigger, str(scrub(goal)[0]),
             json.dumps(policy or {})),
        )
    append_event(run_id, "run", {"goal": goal, "policy": policy or {}, "trigger": trigger})
    return run_id


def append_event(run_id: str, kind: str, payload: dict | None = None) -> int:
    """Append one event (scrubbed at the write boundary). Returns the seq.

    Raises KeyError for unknown run_id, ValueError for unknown kind.
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind: {kind!r}")
    scrubbed, redacted = scrub(dict(payload or {}))
    if redacted:
        scrubbed["_redacted"] = redacted
    with _conn() as conn:
        if not conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone():
            raise KeyError(f"no run {run_id!r}")
        # Atomic seq assignment (same INSERT...SELECT pattern as trace_store).
        conn.execute(
            "INSERT INTO run_events (run_id, seq, ts, kind, payload)"
            " SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ? FROM run_events WHERE run_id = ?",
            (run_id, _now(), kind, json.dumps(scrubbed), run_id),
        )
        seq = int(conn.execute(
            "SELECT MAX(seq) FROM run_events WHERE run_id = ?", (run_id,)).fetchone()[0])
        conn.execute("UPDATE runs SET last_seq = ?, updated_at = ? WHERE run_id = ?",
                     (seq, _now(), run_id))
    return seq


def append_control(run_id: str, action: str, *, text: str = "") -> int:
    """Journal a control intent (pause/resume/kill/steer) for the loop to apply
    at its next round boundary. Never changes status — transitions stay
    single-writer in the loop."""
    if action not in CONTROL_ACTIONS:
        raise ValueError(f"unknown control action: {action!r}")
    payload = {"action": action}
    if text:
        payload["text"] = text
    return append_event(run_id, "control", payload)


def pending_controls(run_id: str, after_seq: int = 0) -> list[dict]:
    """Control intents appended after the caller's consumed cursor, in order."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT seq, payload FROM run_events WHERE run_id = ? AND kind = 'control'"
            " AND seq > ? ORDER BY seq", (run_id, after_seq)
        ).fetchall()
    out = []
    for seq, raw in rows:
        p = json.loads(raw)
        out.append({"seq": int(seq), "action": p.get("action", ""),
                    **({"text": p["text"]} if p.get("text") else {})})
    return out


def latest_control_cursor(run_id: str) -> int:
    """Highest control seq already consumed (from heartbeat markers). Written
    per consumed control so a crash/stop mid-drain never re-applies one."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT payload FROM run_events WHERE run_id = ? AND kind = 'heartbeat'"
            " ORDER BY seq DESC", (run_id,)
        ).fetchall()
    cursor = 0
    for (raw,) in rows:
        try:
            cursor = max(cursor, int(json.loads(raw).get("control_cursor", 0)))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return cursor


def append_round_snapshot(run_id: str, *, round_idx: int, model: str = "",
                          escalated: bool = False, consecutive_errors: int = 0,
                          force_tool_consumed: bool = False,
                          extra: dict | None = None) -> int:
    """Journal the loop state at a round boundary (plan WS1 issue 1).

    A resume restores escalated/consecutive_errors/model/force_tool_consumed
    from `latest_round` so the fallback cascade continues where it left off
    instead of silently resetting. Secrets in `extra` are scrubbed by
    `append_event` on the way in.
    """
    payload = {
        "round_idx": int(round_idx), "model": model,
        "escalated": bool(escalated),
        "consecutive_errors": int(consecutive_errors),
        "force_tool_consumed": bool(force_tool_consumed),
    }
    if extra:
        payload["extra"] = extra
    return append_event(run_id, "round", payload)


def latest_round(run_id: str) -> dict | None:
    """Most recent round-boundary snapshot, or None if the run has no rounds."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT seq, payload FROM run_events WHERE run_id = ? AND kind = 'round'"
            " ORDER BY seq DESC LIMIT 1", (run_id,)
        ).fetchone()
    if row is None:
        return None
    snap = json.loads(row[1])
    snap["seq"] = int(row[0])
    return snap


def get_events(run_id: str, after_seq: int = 0) -> list[dict]:
    """Events after after_seq in seq order — the loop's replay source."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT seq, ts, kind, payload FROM run_events"
            " WHERE run_id = ? AND seq > ? ORDER BY seq",
            (run_id, after_seq),
        ).fetchall()
    return [
        {"seq": r[0], "ts": r[1], "kind": r[2], "payload": json.loads(r[3])}
        for r in rows
    ]


def claim_lease(run_id: str, pid: int, ttl_s: float = 60.0,
                max_attempts: int = _DEFAULT_MAX_ATTEMPTS) -> int:
    """Take execution ownership of a run: bump attempt, record the child pid
    and a lease deadline, transition to running. Over the attempt cap the run
    is failed and dead-lettered (M3). Returns the new attempt count."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT status, attempt FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"no run {run_id!r}")
        status, attempt = row
        attempt += 1
        conn.execute(
            "UPDATE runs SET attempt = ?, pid = ?, lease_expires_at = ?, updated_at = ?"
            " WHERE run_id = ?",
            (attempt, int(pid), time.time() + float(ttl_s), _now(), run_id),
        )
    if status != "running":
        set_status(run_id, "running")
    # Claiming IS a liveness record — a run that finishes in under one
    # heartbeat interval still leaves a lease event behind.
    append_event(run_id, "heartbeat",
                 {"lease": round(time.time() + float(ttl_s)), "attempt": attempt,
                  "pid": int(pid)})
    # max_attempts is the TOTAL number of executions allowed; the claim that
    # would be the (cap+1)th never runs — the run is dead-lettered instead.
    if attempt >= max_attempts:
        reason = f"exceeded {max_attempts} attempts (crashed repeatedly)"
        append_event(run_id, "heartbeat", {"attempt": attempt, "reason": "dead-letter"})
        mark_dead_lettered(run_id, reason)
    return attempt


def renew_lease(run_id: str, ttl_s: float = 60.0) -> float:
    """Extend the lease and journal a heartbeat. Returns the new deadline."""
    deadline = time.time() + float(ttl_s)
    with _conn() as conn:
        conn.execute(
            "UPDATE runs SET lease_expires_at = ?, updated_at = ? WHERE run_id = ?",
            (deadline, _now(), run_id))
    append_event(run_id, "heartbeat",
                 {"lease": round(deadline), "ttl_s": float(ttl_s)})
    return deadline


def release_lease(run_id: str) -> None:
    """Drop pid + lease — the child finished or was reaped."""
    with _conn() as conn:
        conn.execute(
            "UPDATE runs SET pid = NULL, lease_expires_at = 0, updated_at = ?"
            " WHERE run_id = ?", (_now(), run_id))


def stale_runs(status: str = "running") -> list[dict]:
    """Runs whose execution lease has expired (crashed/wedged child)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE status = ? AND lease_expires_at > 0"
            " AND lease_expires_at < ? ORDER BY updated_at",
            (status, time.time())).fetchall()
    return [r for (rid,) in rows if (r := get_run(rid)) is not None]


def mark_dead_lettered(run_id: str, reason: str) -> None:
    """Move an over-attempted run to the dead-letter queue (status failed)."""
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dead_letters (run_id, reason, at) VALUES (?, ?, ?)",
            (run_id, reason, _now()))
    try:
        set_status(run_id, "failed")
    except ValueError:
        pass  # already terminal — the DLQ row is the record


def list_dead_letters(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT run_id, reason, at FROM dead_letters ORDER BY at DESC LIMIT ?",
            (limit,)).fetchall()
    return [{"run_id": r[0], "reason": r[1], "at": r[2]} for r in rows]


def prune_runs(max_runs: int | None = None, max_age_s: int | None = None) -> int:
    """Retention (plan perf budget: 500 runs / 30 days). Keeps the newest
    `max_runs` rows and anything younger than `max_age_s`; ACTIVE runs
    (queued/running/paused/steer-waiting/retrying) are never evicted. Returns
    how many runs were removed."""
    cap = _MAX_RUNS if max_runs is None else max_runs
    age = _MAX_AGE_S if max_age_s is None else max_age_s
    cutoff = time.time() - age
    active = {"queued", "running", "paused", "steer-waiting", "retrying"}
    with _conn() as conn:
        rows = conn.execute(
            "SELECT run_id, status, created_at FROM runs"
            " ORDER BY created_at DESC, rowid DESC"  # rowid tiebreak: same-ms runs
        ).fetchall()  # must order newest-first deterministically
        victims = []
        kept_terminal = 0
        for rid, status, created in rows:
            if status in active:
                continue  # never evict live work; it does not consume the cap
            if kept_terminal < cap and _epoch(created) >= cutoff:
                kept_terminal += 1
                continue
            victims.append(rid)
        for rid in victims:
            conn.execute("DELETE FROM run_events WHERE run_id = ?", (rid,))
            conn.execute("DELETE FROM runs WHERE run_id = ?", (rid,))
    return len(victims)


def _epoch(value) -> float:
    """created_at is stored as an ISO string; tolerate epoch floats too."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return time.time()


def get_run(run_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT run_id, created_at, updated_at, trigger, goal, policy,"
            " status, attempt, last_seq, pid, lease_expires_at"
            " FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "run_id": row[0], "created_at": row[1], "updated_at": row[2],
        "trigger": row[3], "goal": row[4], "policy": json.loads(row[5]),
        "status": row[6], "attempt": row[7], "last_seq": row[8],
        "pid": row[9], "lease_expires_at": row[10] or 0,
        "resumable": row[6] in _RESUMABLE,
    }


def set_status(run_id: str, status: str) -> str:
    """Transition status with validation; appends a `status` event."""
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status!r}")
    with _conn() as conn:
        row = conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"no run {run_id!r}")
        current = row[0]
        if status not in _TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid transition {current!r} -> {status!r}")
        conn.execute("UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                     (status, _now(), run_id))
    append_event(run_id, "status", {"status": status, "from": current})
    return status


def record_attempt(run_id: str) -> int:
    """Child crashed / retry tick — increments attempt, moves `running` runs
    to `retrying`. Returns the new attempt count."""
    with _conn() as conn:
        conn.execute("UPDATE runs SET attempt = attempt + 1, updated_at = ? WHERE run_id = ?",
                     (_now(), run_id))
        row = conn.execute("SELECT attempt, status FROM runs WHERE run_id = ?",
                           (run_id,)).fetchone()
    attempt, status = row if row else (0, "queued")
    if status == "running":
        try:
            set_status(run_id, "retrying")
        except ValueError:
            pass
    append_event(run_id, "heartbeat", {"attempt": attempt, "reason": "retry"})
    return attempt


def list_runs(status: str | None = None, limit: int = 50) -> list[dict]:
    q = "SELECT run_id FROM runs"
    params: tuple = ()
    if status:
        q += " WHERE status = ?"
        params = (status,)
    q += " ORDER BY updated_at DESC LIMIT ?"
    with _conn() as conn:
        ids = [r[0] for r in conn.execute(q, (*params, limit)).fetchall()]
    return [run for rid in ids if (run := get_run(rid)) is not None]
