"""
Durable workflow, approval, and notification persistence.

This store is the foundation for long-running hands-free automations:
scheduled workflows, approval checkpoints, and notification history.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from store import dbclose
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger

DB_PATH = Path(__file__).parent / "workflow_state.db"

_log = logging.getLogger(__name__)

_CREATE = """
CREATE TABLE IF NOT EXISTS workflow_instances (
    id               TEXT PRIMARY KEY,
    automation_id    TEXT NOT NULL,
    task_name        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    schedule_json    TEXT NOT NULL DEFAULT '{}',
    config_json      TEXT NOT NULL DEFAULT '{}',
    last_run_at      TEXT,
    next_run_at      TEXT,
    last_result_json TEXT NOT NULL DEFAULT '{}',
    dedupe_key       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id                   TEXT PRIMARY KEY,
    workflow_instance_id TEXT NOT NULL DEFAULT '',
    job_id               TEXT NOT NULL DEFAULT '',
    kind                 TEXT NOT NULL,
    payload_json         TEXT NOT NULL DEFAULT '{}',
    status               TEXT NOT NULL DEFAULT 'pending',
    expires_at           TEXT,
    resolved_at          TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id                  TEXT PRIMARY KEY,
    ts                  INTEGER NOT NULL,
    level               TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT '',
    message             TEXT NOT NULL,
    action_label        TEXT NOT NULL DEFAULT '',
    action_url          TEXT NOT NULL DEFAULT '',
    action_type         TEXT NOT NULL DEFAULT '',
    action_payload_json TEXT NOT NULL DEFAULT '{}',
    read                INTEGER NOT NULL DEFAULT 0
);
"""


_MIGRATE = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_dedupe
  ON workflow_instances(dedupe_key) WHERE dedupe_key != '';
"""

_ADD_COLS = [
    "ALTER TABLE workflow_instances ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'schedule'",
    "ALTER TABLE workflow_instances ADD COLUMN action_type  TEXT NOT NULL DEFAULT 'notification'",
    "ALTER TABLE workflow_instances ADD COLUMN run_history_json TEXT NOT NULL DEFAULT '[]'",
]

_RUN_HISTORY_CAP = 100


def _conn() -> sqlite3.Connection:
    conn = dbclose.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_load(raw: str, fallback):
    try:
        return json.loads(raw)
    except Exception:
        _log.debug("JSON parse failed in _json_load", exc_info=True)
        return fallback


def init() -> None:
    with _conn() as conn:
        conn.executescript(_CREATE)
        for stmt in _ADD_COLS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        conn.executescript(_MIGRATE)


def create_workflow_instance(
    *,
    workflow_id: str | None = None,
    automation_id: str,
    task_name: str,
    config: dict | None = None,
    schedule: dict | None = None,
    status: str = "active",
    next_run_at: str | None = None,
    dedupe_key: str = "",
    trigger_type: str = "schedule",
    action_type: str = "notification",
) -> dict:
    workflow_id = workflow_id or uuid.uuid4().hex
    now = _now()
    schedule = schedule or {}
    if next_run_at is None:
        next_run_at = _initial_next_run(schedule)
    # ponytail: manual/webhook workflows are externally triggered and don't
    # need a schedule. Only warn when a polling-type creation ends up dead.
    if next_run_at is None and status == "active" and trigger_type in ("schedule", "email"):
        _log.info("create_workflow_instance: no schedule for %s — setting status=paused", task_name)
        status = "paused"
    row = {
        "id": workflow_id,
        "automation_id": automation_id,
        "task_name": task_name,
        "status": status,
        "schedule_json": json.dumps(schedule, sort_keys=True),
        "config_json": json.dumps(config or {}, sort_keys=True),
        "last_run_at": None,
        "next_run_at": next_run_at,
        "last_result_json": "{}",
        "dedupe_key": dedupe_key,
        "trigger_type": trigger_type,
        "action_type": action_type,
        "created_at": now,
        "updated_at": now,
    }
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO workflow_instances (
                id, automation_id, task_name, status, schedule_json, config_json,
                last_run_at, next_run_at, last_result_json, dedupe_key,
                trigger_type, action_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["automation_id"],
                row["task_name"],
                row["status"],
                row["schedule_json"],
                row["config_json"],
                row["last_run_at"],
                row["next_run_at"],
                row["last_result_json"],
                row["dedupe_key"],
                row["trigger_type"],
                row["action_type"],
                row["created_at"],
                row["updated_at"],
            ),
        )
    return get_workflow_instance(workflow_id) or {}


def _resolve_id(identifier: str) -> str:
    """Accept either the instance uuid or its task_name and return the uuid.
    CRUD tools are LLM-driven and are frequently called with the human-readable
    name instead of the uuid — resolve once here instead of failing silently
    at every callsite.
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM workflow_instances WHERE id = ? OR task_name = ? COLLATE NOCASE",
            (identifier, identifier),
        ).fetchone()
    return row["id"] if row else identifier


def get_workflow_instance(workflow_id: str) -> dict | None:
    workflow_id = _resolve_id(workflow_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM workflow_instances WHERE id = ?",
            (workflow_id,),
        ).fetchone()
    if not row:
        return None
    return _decode_workflow(dict(row))


def list_workflow_instances(
    *,
    automation_id: str = "",
    status: str = "",
    trigger_type: str = "",
    limit: int = 50,
) -> list[dict]:
    query = "SELECT * FROM workflow_instances"
    clauses: list[str] = []
    params: list[object] = []
    if automation_id:
        clauses.append("automation_id = ?")
        params.append(automation_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if trigger_type:
        clauses.append("trigger_type = ?")
        params.append(trigger_type)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_decode_workflow(dict(row)) for row in rows]


def update_workflow_instance(
    workflow_id: str,
    *,
    status: str | None = None,
    schedule: dict | None = None,
    config: dict | None = None,
    last_run_at: str | None = None,
    next_run_at: str | None = None,
    last_result: dict | None = None,
    dedupe_key: str | None = None,
    trigger_type: str | None = None,
    action_type: str | None = None,
) -> dict | None:
    workflow_id = _resolve_id(workflow_id)
    current = get_workflow_instance(workflow_id)
    if not current:
        return None

    updates: list[str] = []
    params: list[object] = []
    if status is not None:
        if status == "active" and current.get("next_run_at") is None and next_run_at is None \
                and current.get("trigger_type") in ("schedule", "email"):
            _log.info("update_workflow_instance: no next_run for %s — pausing instead of activating", current["task_name"])
            status = "paused"
        updates.append("status = ?")
        params.append(status)
    if schedule is not None:
        updates.append("schedule_json = ?")
        params.append(json.dumps(schedule, sort_keys=True))
    if config is not None:
        updates.append("config_json = ?")
        params.append(json.dumps(config, sort_keys=True))
    if last_run_at is not None:
        updates.append("last_run_at = ?")
        params.append(last_run_at)
    if next_run_at is not None:
        updates.append("next_run_at = ?")
        params.append(next_run_at)
    if last_result is not None:
        updates.append("last_result_json = ?")
        params.append(json.dumps(last_result, sort_keys=True))
    if dedupe_key is not None:
        updates.append("dedupe_key = ?")
        params.append(dedupe_key)
    if trigger_type is not None:
        updates.append("trigger_type = ?")
        params.append(trigger_type)
    if action_type is not None:
        updates.append("action_type = ?")
        params.append(action_type)
    updates.append("updated_at = ?")
    params.append(_now())
    params.append(workflow_id)

    with _conn() as conn:
        conn.execute(
            f"UPDATE workflow_instances SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    return get_workflow_instance(workflow_id)


def append_run_history(workflow_id: str, result: dict) -> None:
    """Append one run result to the capped run-history list, additive to
    (not a replacement for) last_result_json — kept as a separate column so
    every existing reader of last_result's single-dict shape is unaffected.
    """
    workflow_id = _resolve_id(workflow_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT run_history_json FROM workflow_instances WHERE id = ?", (workflow_id,),
        ).fetchone()
        if row is None:
            return
        history = _json_load(row["run_history_json"], [])
        history.append({"ts": _now(), **result})
        history = history[-_RUN_HISTORY_CAP:]
        conn.execute(
            "UPDATE workflow_instances SET run_history_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(history, sort_keys=True, default=str), _now(), workflow_id),
        )


def pause_workflow_instance(workflow_id: str) -> dict | None:
    return update_workflow_instance(workflow_id, status="paused")


def resume_workflow_instance(workflow_id: str) -> dict | None:
    current = get_workflow_instance(workflow_id)
    if not current:
        return None
    next_run_at = current.get("next_run_at") or _compute_next_run(current.get("schedule", {}))
    return update_workflow_instance(workflow_id, status="active", next_run_at=next_run_at)


def delete_workflow_instance(workflow_id: str) -> bool:
    workflow_id = _resolve_id(workflow_id)
    with _conn() as conn:
        conn.execute("DELETE FROM approval_requests WHERE workflow_instance_id = ?", (workflow_id,))
        cur = conn.execute("DELETE FROM workflow_instances WHERE id = ?", (workflow_id,))
        return cur.rowcount > 0


def trigger_workflow_instance(workflow_id: str) -> dict | None:
    current = get_workflow_instance(workflow_id)
    if not current:
        return None
    return update_workflow_instance(
        workflow_id,
        status="active",
        next_run_at=_utcnow().isoformat(),
    )


def claim_due_workflows(limit: int = 10) -> list[dict]:
    """
    Atomically claim active workflows whose next_run_at is due.

    Recurring workflows advance to their next scheduled run immediately so the
    single task worker can safely enqueue them without per-workflow plists.
    One-shot workflows are paused after dispatch.
    """
    now = _utcnow()
    claimed: list[dict] = []
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT * FROM workflow_instances
            WHERE status = 'active'
              AND next_run_at IS NOT NULL
              AND next_run_at <= ?
            ORDER BY next_run_at ASC
            LIMIT ?
            """,
            (now.isoformat(), limit),
        ).fetchall()
        for row in rows:
            item = _decode_workflow(dict(row))
            item["last_run_at"] = item.get("next_run_at")
            prev_time = _parse_iso(item["next_run_at"]) if item.get("next_run_at") else None
            is_overdue = prev_time and (now - prev_time) > timedelta(minutes=15)
            next_run = _compute_next_run(
                item["schedule"],
                now=now,
                previous_fire_time=None if is_overdue else prev_time,
            )
            status = "active" if next_run else "paused"
            conn.execute(
                """
                UPDATE workflow_instances
                SET status = ?, next_run_at = ?, last_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    next_run,
                    item["last_run_at"],
                    now.isoformat(),
                    item["id"],
                ),
            )
            item["status"] = status
            item["next_run_at"] = next_run
            claimed.append(item)
    return claimed


def create_approval_request(
    *,
    kind: str,
    payload: dict | None = None,
    workflow_instance_id: str = "",
    job_id: str = "",
    expires_at: str | None = None,
) -> dict:
    approval_id = uuid.uuid4().hex
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO approval_requests (
                id, workflow_instance_id, job_id, kind, payload_json, status,
                expires_at, resolved_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL, ?, ?)
            """,
            (
                approval_id,
                workflow_instance_id,
                job_id,
                kind,
                json.dumps(payload or {}, sort_keys=True),
                expires_at,
                now,
                now,
            ),
        )
    return get_approval_request(approval_id) or {}


def get_approval_request(approval_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM approval_requests WHERE id = ?",
            (approval_id,),
        ).fetchone()
    if not row:
        return None
    return _decode_approval(dict(row))


def list_approval_requests(*, status: str = "", limit: int = 50) -> list[dict]:
    query = "SELECT * FROM approval_requests"
    params: list[object] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_decode_approval(dict(row)) for row in rows]


def resolve_approval_request(approval_id: str, status: str) -> dict | None:
    if status not in {"approved", "rejected", "expired"}:
        raise ValueError(status)
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            UPDATE approval_requests
            SET status = ?, resolved_at = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (status, now, now, approval_id),
        )
    return get_approval_request(approval_id)


def add_notification(
    *,
    message: str,
    level: str = "info",
    source: str = "",
    action_label: str = "",
    action_url: str = "",
    action_type: str = "",
    action_payload: dict | None = None,
) -> dict:
    notif_id = uuid.uuid4().hex[:12]
    ts = int(datetime.now(timezone.utc).timestamp())
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO notifications (
                id, ts, level, source, message,
                action_label, action_url, action_type, action_payload_json, read
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                notif_id,
                ts,
                level,
                source,
                message,
                action_label,
                action_url,
                action_type,
                json.dumps(action_payload or {}, sort_keys=True),
            ),
        )
    return get_notification(notif_id) or {}


def get_notification(notif_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM notifications WHERE id = ?",
            (notif_id,),
        ).fetchone()
    if not row:
        return None
    return _decode_notification(dict(row))


def list_notifications(*, unread_only: bool = False) -> list[dict]:
    query = "SELECT * FROM notifications"
    params: list[object] = []
    if unread_only:
        query += " WHERE read = 0"
    query += " ORDER BY ts DESC"
    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_decode_notification(dict(row)) for row in rows]


def mark_notification_read(notif_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE notifications SET read = 1 WHERE id = ?",
            (notif_id,),
        )
    return cur.rowcount > 0


def delete_notification(notif_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM notifications WHERE id = ?", (notif_id,))
    return cur.rowcount > 0


def clear_notifications() -> int:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM notifications")
    return cur.rowcount


def count_unread_notifications() -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM notifications WHERE read = 0"
        ).fetchone()
    return int(row["count"]) if row else 0


def _is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return False
    return True


def _user_timezone() -> str:
    """Resolve the user's preferred IANA timezone for cron schedules."""
    for raw in (os.environ.get("USER_TIMEZONE", ""), os.environ.get("TZ", "")):
        name = raw.strip()
        if name and _is_valid_timezone(name):
            return name

    try:
        localtime = Path("/etc/localtime").resolve()
        parts = localtime.parts
        if "zoneinfo" in parts:
            idx = parts.index("zoneinfo")
            name = "/".join(parts[idx + 1 :])
            if name and _is_valid_timezone(name):
                return name
    except Exception:
        _log.debug("timezone detection failed", exc_info=True)
    return "America/New_York"


def _cron_schedule(expr: str) -> dict:
    return {"cron": expr, "timezone": _user_timezone()}


_BUILTIN_WORKFLOWS = [
    {
        "automation_id": "email.watch_sender",
        "task_name":     "Watch sender emails",
        "trigger_type":  "email",
        "action_type":   "notification",
        "schedule":      {"interval_seconds": 60},
        "config":        {"senders": []},
        "dedupe_key":    "email.watch_sender",
    },
    {
        "automation_id": "email.daily_summary",
        "task_name":     "Daily email summary",
        "trigger_type":  "schedule",
        "action_type":   "notification",
        "schedule":      _cron_schedule("30 6 * * *"),
        "config":        {},
        "dedupe_key":    "email.daily_summary",
    },
    {
        "automation_id": "email.attachment_archive",
        "task_name":     "Archive all inbox attachments",
        "trigger_type":  "schedule",
        "action_type":   "notification",
        "schedule":      {"interval_seconds": 3600},
        "config":        {},
        "dedupe_key":    "email.attachment_archive",
    },
    {
        "automation_id": "inbox.monitor_attachments",
        "task_name":     "Monitor inbox attachments",
        "trigger_type":  "email",
        "action_type":   "notification",
        "schedule":      {"interval_seconds": 300},
        "config":        {"senders": []},
        "dedupe_key":    "inbox.monitor_attachments",
    },
    {
        "automation_id": "briefing.morning",
        "task_name":     "Morning briefing",
        "trigger_type":  "schedule",
        "action_type":   "telegram",
        "schedule":      _cron_schedule("0 8 * * *"),
        "config":        {},
        "dedupe_key":    "briefing.morning",
    },
    {
        "automation_id": "study.usmle_daily",
        "task_name":     "USMLE daily questions",
        "trigger_type":  "schedule",
        "action_type":   "telegram",
        "schedule":      _cron_schedule("0 7 * * *"),
        "config":        {},
        "dedupe_key":    "study.usmle_daily",
    },
    {
        "automation_id": "tech_brief",
        "task_name":     "Tech news briefing",
        "trigger_type":  "schedule",
        "action_type":   "telegram",
        "schedule":      _cron_schedule("0 7,18 * * *"),
        "config":        {"email": "jimkalinov@gmail.com"},
        "dedupe_key":    "tech_brief",
    },
    {
        "automation_id": "golden.queries",
        "task_name":     "Golden query regression suite",
        "trigger_type":  "schedule",
        "action_type":   "notification",
        "schedule":      _cron_schedule("0 3 * * *"),
        "config":        {},
        "dedupe_key":    "golden.queries",
    },
    {
        "automation_id": "health_check",
        "task_name":     "Workflow health check",
        "trigger_type":  "schedule",
        "action_type":   "telegram",
        "schedule":      _cron_schedule("0 9 * * *"),
        "config":        {"include_ok": False},
        "dedupe_key":    "health_check",
    },
    {
        "automation_id": "reddit_intel",
        "task_name":     "Reddit market-intel scan",
        "trigger_type":  "schedule",
        "action_type":   "notification",
        # 6h initial fallback — handler self-tunes next_run_at after each run
        # via its own adaptive-cadence override (jobs/handlers/reddit_intel.py).
        "schedule":      {"interval_seconds": 21600},
        "config":        {},
        "dedupe_key":    "reddit_intel",
    },
    {
        "automation_id": "subagent_audit",
        "task_name":     "Subagent judgment audit",
        "trigger_type":  "schedule",
        "action_type":   "notification",
        "schedule":      {"interval_seconds": 604800},  # weekly
        "config":        {},
        "dedupe_key":    "subagent_audit",
    },
    {
        "automation_id": "daily_digest",
        "task_name":     "Daily subagent digest",
        "trigger_type":  "schedule",
        "action_type":   "notification",
        "schedule":      _cron_schedule("0 20 * * *"),  # 8pm ET, after the day's jobs run
        "config":        {},
        "dedupe_key":    "daily_digest",
    },
    {
        "automation_id": "science_scout",
        "task_name":     "Science-niche breakthrough scout",
        "trigger_type":  "schedule",
        "action_type":   "notification",
        "schedule":      {"interval_seconds": 1800},  # every 30 min
        "config":        {},
        "dedupe_key":    "science_scout",
    },
    {
        "automation_id": "world_monitor",
        "task_name":     "World Monitor global intelligence scan",
        "trigger_type":  "schedule",
        "action_type":   "notification",
        "schedule":      {"interval_seconds": 1800},  # every 30 min, staggered vs science_scout (below)
        "config":        {"poll_hours": 0.5},
        "dedupe_key":    "world_monitor",
    },
]


def seed_builtins() -> None:
    """Idempotently seed builtin workflow instances. Safe to call on every startup."""
    init()
    for w in _BUILTIN_WORKFLOWS:
        try:
            next_run_at = None
            if w["dedupe_key"] == "world_monitor":
                # stagger vs science_scout — same 1800s interval, avoid co-incident FD spikes
                next_run_at = (_utcnow() + timedelta(seconds=900)).isoformat()
            create_workflow_instance(
                automation_id=w["automation_id"],
                task_name=w["task_name"],
                trigger_type=w["trigger_type"],
                action_type=w["action_type"],
                schedule=w["schedule"],
                config=w["config"],
                status="active",
                dedupe_key=w["dedupe_key"],
                next_run_at=next_run_at,
            )
        except sqlite3.IntegrityError:
            pass  # dedupe_key unique index — row already seeded
        except Exception as exc:
            _log.warning("seed_builtins: failed to seed %s: %s", w["dedupe_key"], exc)

    _migrate_missing_cron_timezones()


def _migrate_missing_cron_timezones() -> None:
    """Backfill timezone on existing cron workflows without changing status."""
    tz = _user_timezone()
    now = _utcnow()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM workflow_instances WHERE schedule_json LIKE '%cron%'"
        ).fetchall()
        for row in rows:
            item = _decode_workflow(dict(row))
            schedule = dict(item.get("schedule") or {})
            if not schedule.get("cron") or schedule.get("timezone"):
                continue
            schedule["timezone"] = tz
            next_run_at = item.get("next_run_at")
            if item.get("status") == "active":
                next_run_at = _compute_next_run(schedule, now=now)
            conn.execute(
                """
                UPDATE workflow_instances
                SET schedule_json = ?, next_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(schedule, sort_keys=True),
                    next_run_at,
                    now.isoformat(),
                    item["id"],
                ),
            )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _initial_next_run(schedule: dict) -> str | None:
    interval = int(schedule.get("interval_seconds", 0) or 0)
    if interval > 0:
        return (_utcnow() + timedelta(seconds=interval)).isoformat()
    raw = (schedule.get("run_at") or "").strip()
    if raw:
        dt = _parse_iso(raw)
        if dt:
            return dt.isoformat()
    cron = (schedule.get("cron") or "").strip()
    if cron:
        return _compute_next_run(schedule)
    return None


def _compute_next_run(
    schedule: dict,
    *,
    now: datetime | None = None,
    previous_fire_time: datetime | None = None,
) -> str | None:
    now = now or _utcnow()
    interval = int(schedule.get("interval_seconds", 0) or 0)
    if interval > 0:
        return (now + timedelta(seconds=interval)).isoformat()
    cron = (schedule.get("cron") or "").strip()
    if cron:
        return _compute_next_cron_run(
            cron,
            timezone_name=schedule.get("timezone") or _user_timezone(),
            now=now,
            previous_fire_time=previous_fire_time,
        )
    return None


def _parse_iso(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _compute_next_cron_run(
    cron: str,
    *,
    timezone_name: str,
    now: datetime,
    previous_fire_time: datetime | None = None,
) -> str | None:
    try:
        trigger = CronTrigger.from_crontab(cron, timezone=timezone_name or "UTC")
    except Exception:
        _log.warning("CronTrigger from_crontab failed for '%s' in %s", cron, timezone_name, exc_info=True)
        return None
    next_fire = trigger.get_next_fire_time(previous_fire_time, now)
    if next_fire is None:
        return None
    return next_fire.astimezone(timezone.utc).isoformat()


def validate_cron(cron: str, timezone_name: str | None = None) -> str | None:
    """Return an error message if `cron` is malformed, else None.

    2026-07-11: _compute_next_cron_run() swallows a bad cron into a silent
    next_run_at=None, and claim_due_workflows() filters on `next_run_at IS
    NOT NULL` — a malformed cron used to create a workflow that reported
    "created successfully" but would never fire, with no error anywhere.
    Callers should validate before create_workflow_instance(), not after.
    """
    try:
        CronTrigger.from_crontab(cron, timezone=timezone_name or "UTC")
    except Exception as exc:
        return f"invalid cron expression {cron!r}: {exc}"
    return None


def _decode_workflow(row: dict) -> dict:
    row["schedule"] = _json_load(row.pop("schedule_json", "{}"), {})
    row["config"] = _json_load(row.pop("config_json", "{}"), {})
    row["last_result"] = _json_load(row.pop("last_result_json", "{}"), {})
    row["run_history"] = _json_load(row.pop("run_history_json", "[]"), [])
    row.setdefault("trigger_type", "schedule")
    row.setdefault("action_type", "notification")
    return row


def _decode_approval(row: dict) -> dict:
    row["payload"] = _json_load(row.pop("payload_json", "{}"), {})
    return row


def _decode_notification(row: dict) -> dict:
    row["action_payload"] = _json_load(row.pop("action_payload_json", "{}"), {})
    row["read"] = bool(row.get("read"))
    return row
