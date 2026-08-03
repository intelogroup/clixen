"""Health check for all workflows/automations.

Scans every workflow_instance in workflow_store and reports:
  - which are active/paused
  - which have never run (no last_run_at)
  - which errored on their last run (success=False / error in last_result)
  - which are overdue (next_run_at in the past and status=active)
  - which handler is missing from the registry (dangling automation_id)

Can target a single workflow via instance["config"]["workflow_id"], else all.

instance["config"] keys:
  workflow_id   optional str — check only this instance (uuid or task_name); omit for all
  include_ok    bool         — if true, list healthy workflows too (default: only problems)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

_log = logging.getLogger(__name__)

_HEALTHY_STATUSES = {"active", "paused"}


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _is_overdue(instance: dict, now: datetime) -> bool:
    if instance.get("status") != "active":
        return False
    nxt = _parse_ts(instance.get("next_run_at"))
    return nxt is not None and nxt < now


def _last_run_failed(instance: dict) -> bool:
    result = instance.get("last_result") or {}
    if not isinstance(result, dict):
        return False
    if result.get("success") is False:
        return True
    # Some handlers stash an error string instead of {success:False}
    if isinstance(result.get("error"), str) and result["error"].strip():
        return True
    return False


def handle(instance: dict) -> dict:
    from store import workflow_store
    from jobs import handler_registry

    config = dict(instance.get("config") or {})
    target = config.get("workflow_id")
    include_ok = bool(config.get("include_ok", False))

    if target:
        instances = [workflow_store.get_workflow_instance(target)]
        instances = [i for i in instances if i]
    else:
        instances = workflow_store.list_workflow_instances(limit=1000)

    if not instances:
        return {"success": True, "checked": 0, "problems": 0, "detail": "no workflows found"}

    registered = set(handler_registry.registered_ids())
    now = datetime.now(timezone.utc)

    problems: list[dict] = []
    healthy: list[dict] = []

    own_id = instance.get("id", "")
    for w in instances:
        if w.get("id") == own_id:
            continue
        aid = w.get("automation_id", "")
        status = w.get("status", "")
        last_run = _parse_ts(w.get("last_run_at"))
        issue = None

        if aid not in registered:
            issue = "no handler registered"
        elif status not in _HEALTHY_STATUSES:
            issue = f"bad status: {status!r}"
        elif last_run is None:
            nxt = _parse_ts(w.get("next_run_at"))
            if nxt is not None and nxt > now:
                pass  # first scheduled run still in the future — not yet overdue
            else:
                issue = "never run"
        elif _last_run_failed(w):
            issue = "last run failed"
        elif _is_overdue(w, now):
            issue = "overdue"

        entry = {
            "id": w.get("id"),
            "task_name": w.get("task_name"),
            "automation_id": aid,
            "status": status,
            "last_run_at": w.get("last_run_at"),
            "next_run_at": w.get("next_run_at"),
        }
        if issue:
            entry["issue"] = issue
            if _last_run_failed(w):
                entry["last_error"] = (w.get("last_result") or {}).get("error", "")
            problems.append(entry)
        else:
            healthy.append(entry)

    total = len(healthy) + len(problems)
    shown = problems if not include_ok else (problems + healthy)

    summary = (
        f"{len(problems)} problem(s) of {total} workflow(s)"
        + ("" if not include_ok else f" — {len(healthy)} healthy")
    )

    return {
        "success": not problems,
        "checked": total,
        "problems": len(problems),
        "healthy": len(healthy),
        "detail": summary,
        "workflows": shown,
        # success=False lets the worker surface this via the normal failure path
        # if you wire health_check as a scheduled alert; for on-demand checks the
        # detail + workflows list is the payload.
        "error": None if not problems else summary,
        # human-readable message for arbiter to digest
        "telegram_message": "\n".join(
            [f"Workflow Health — {summary}"]
            + ([f"{p['task_name']} ({p['automation_id']}) — {p['issue']}" + (f": {p['last_error']}" if p.get("last_error") else "") for p in problems]
               if problems else ["All workflows healthy"])
        ) if instance.get("action_type") == "telegram" else None,
    }
