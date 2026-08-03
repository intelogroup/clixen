"""Handler: subagent_audit

Weekly review of what a background subagent has been burying (IGNORE'd merge
decisions, suppressed notify_gate alerts) — judges the subagent's own
behavior and either auto-tunes its numeric thresholds, auto-rewrites its
system prompts, or escalates to the user to review it manually.

Every branch produces a notification (auto_tune/auto_rewrite/escalate) or is
a genuine no-op (action="none") — nothing self-modifies silently. A
low-confidence verdict is always forced to escalate instead of acting,
regardless of what action the model named, since an audit that isn't sure of
its own diagnosis must not touch the pipeline it's diagnosing.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

_log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.7

_AUDIT_SYSTEM_PROMPT = """You audit a background research subagent's judgment. You are given a \
sample of items it chose to IGNORE, findings it suppressed from alerting the user, its current \
numeric thresholds, and its current prompts. Decide whether the subagent is behaving well or \
missing real signal.

Respond with ONLY JSON:
{"diagnosis": "one or two sentences", "confidence": 0.0-1.0,
 "action": "none|auto_tune|auto_rewrite_prompt|escalate",
 "threshold_changes": {"key": new_value, ...},
 "prompt_name": "merge_decision|discovery",
 "new_prompt": "full replacement prompt text, only used for auto_rewrite_prompt",
 "user_message": "what to tell the user, only used for escalate"}

Only propose auto_tune/auto_rewrite_prompt if you're genuinely confident (>0.7) — otherwise
choose escalate so a human reviews it."""

# subagent_id -> (store module import path, config keys with their hardcoded defaults, prompt names)
_AUDIT_TARGETS = {
    "reddit_intel": {
        "store_module": "store.reddit_intel_store",
        "config_defaults": {
            "near_dup_max_distance": 0.65,
            "min_active_subreddits": 4,
            "dead_scan_threshold": 5,
            "discovery_cooldown_hours": 1,
        },
        "prompt_names": ["merge_decision", "discovery"],
    },
    "science_scout": {
        "store_module": "store.science_scout_store",
        "config_defaults": {
            "near_dup_max_distance": 0.65,
        },
        "prompt_names": ["merge_decision"],
    },
}


def _gather_context(subagent_id: str, target: dict, since_iso: str) -> dict:
    store = __import__(target["store_module"], fromlist=["_"])
    return {
        "ignored_items": [
            {"item_id": r.get("item_id") or r.get("paper_id"), "created_at": r["created_at"]}
            for r in store.list_links_by_decision("IGNORE", since=since_iso, limit=30)
        ],
        "suppressed_alerts": [
            {"finding": r["finding"][:300], "reason": r["reason"]}
            for r in store.list_suppressed_alerts(since=since_iso, limit=30)
        ],
        "current_config": {
            key: store.get_config(key, default) for key, default in target["config_defaults"].items()
        },
        "current_prompts": {
            name: store.get_prompt(name, "(no override — using hardcoded default)")
            for name in target["prompt_names"]
        },
    }


def _parse_verdict(resp: str) -> dict:
    start, end = resp.find("{"), resp.rfind("}")
    return json.loads(resp[start:end + 1])


def audit_one(subagent_id: str, dry_run: bool = False) -> dict:
    target = _AUDIT_TARGETS[subagent_id]
    store = __import__(target["store_module"], fromlist=["_"])
    from store.workflow_store import add_notification

    since_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    context = _gather_context(subagent_id, target, since_iso)

    if not context["ignored_items"] and not context["suppressed_alerts"]:
        return {"subagent_id": subagent_id, "action": "none", "reason": "nothing to review"}

    from clients.cloud_client import chat
    from clients.cost_guard import BudgetExceededError

    try:
        resp = chat(
            user_message=json.dumps(context),
            system_prompt=_AUDIT_SYSTEM_PROMPT,
            reasoning_effort="medium",
        )
        verdict = _parse_verdict(resp)
    except (BudgetExceededError, ValueError, json.JSONDecodeError):
        _log.warning("[subagent_audit] audit call failed for %s, escalating", subagent_id, exc_info=True)
        add_notification(
            level="warning", source=f"subagent_audit.{subagent_id}",
            message=f"Audit of {subagent_id} failed to run — please check it manually.",
            action_label="Review subagent",
        )
        return {"subagent_id": subagent_id, "action": "escalate", "reason": "audit call failed"}

    action = verdict.get("action", "none")
    confidence = float(verdict.get("confidence", 0) or 0)
    diagnosis = verdict.get("diagnosis", "")

    if action == "auto_rewrite_prompt":
        action = "escalate"
        verdict["user_message"] = (
            f"Audit of {subagent_id} proposed a prompt rewrite — requires human review."
        )

    if action in ("auto_tune",) and confidence < CONFIDENCE_THRESHOLD:
        action = "escalate"
        verdict["user_message"] = (
            f"Audit of {subagent_id} found a possible issue but wasn't confident enough to "
            f"self-correct (confidence={confidence:.2f}): {diagnosis}"
        )

    if action == "auto_tune":
        changes = verdict.get("threshold_changes", {}) or {}
        diffs = []
        for key, new_value in changes.items():
            if key not in target["config_defaults"]:
                continue
            old_value = store.get_config(key, target["config_defaults"][key])
            if not dry_run:
                store.set_config(key, new_value)
            diffs.append(f"{key}: {old_value} -> {new_value}")
        add_notification(
            level="info", source=f"subagent_audit.{subagent_id}",
            message=f"Auto-tuned {subagent_id}: {'; '.join(diffs) or 'no valid keys'}. Reason: {diagnosis}",
        )
    elif action == "auto_rewrite_prompt":
        name = verdict.get("prompt_name", "")
        new_prompt = verdict.get("new_prompt", "")
        if name in target["prompt_names"] and new_prompt:
            old_prompt = store.get_prompt(name, "(hardcoded default)")
            version = store.set_prompt(name, new_prompt, created_by="auto-audit")
            add_notification(
                level="info", source=f"subagent_audit.{subagent_id}",
                message=(
                    f"Auto-rewrote {subagent_id}'s '{name}' prompt to v{version}. Reason: {diagnosis}\n\n"
                    f"Old: {old_prompt[:300]}\n\nNew: {new_prompt[:300]}"
                ),
                action_label="Review change",
            )
        else:
            action = "none"
    elif action == "escalate":
        add_notification(
            level="warning", source=f"subagent_audit.{subagent_id}",
            message=verdict.get("user_message") or f"Audit of {subagent_id}: {diagnosis}",
            action_label="Review subagent",
        )

    return {"subagent_id": subagent_id, "action": action, "confidence": confidence, "diagnosis": diagnosis}


def handle(instance: dict) -> dict:
    cfg = instance.get("config", {})
    targets = cfg.get("subagent_ids") or list(_AUDIT_TARGETS.keys())
    dry_run = cfg.get("dry_run", False)
    results = [audit_one(sid, dry_run=dry_run) for sid in targets if sid in _AUDIT_TARGETS]
    return {"success": True, "audits": results}
