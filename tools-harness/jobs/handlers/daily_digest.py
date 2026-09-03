"""Handler: daily_digest

Daily rollup of what every scheduled workflow did in the last run: reads
last_result_json across all active workflow_instances, LLM-summarizes into
one Telegram message. Pure visibility — no judgment/tuning (that's
subagent_audit's job, and only applies to the 2 workflows that keep a
judgment log).

instance["config"] keys: none.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

_DIGEST_SYSTEM_PROMPT = """You write a short daily ops digest for scheduled background jobs. \
Given each job's name and its last result, produce 3-8 terse bullet lines: what ran, what it \
found (counts/highlights only, not full content), and flag anything that looks broken (errored, \
stale, empty when it shouldn't be). Skip jobs with nothing notable. No preamble, no markdown \
headers, just bullet lines starting with "-"."""


def handle(instance: dict) -> dict:
    from store import workflow_store
    from tools.telegram_send import send_telegram

    instances = workflow_store.list_workflow_instances(status="active", limit=100)
    lines = []
    for inst in instances:
        result = inst.get("last_result") or {}
        if not result:
            continue
        lines.append(f"{inst['task_name']} (last_run={inst.get('last_run_at')}): {result}")

    if not lines:
        return {"success": True, "items_processed": 0, "note": "no workflow results to digest"}

    context = "\n".join(lines)
    try:
        from clients.cloud_client import chat
        summary = chat(context, system_prompt=_DIGEST_SYSTEM_PROMPT).strip()
    except Exception as exc:
        _log.error("daily_digest: LLM summarize failed: %s", exc)
        return {"success": False, "items_processed": len(lines), "error": str(exc)}

    send_telegram(f"Daily subagent digest:\n\n{summary}"[:4000])
    return {"success": True, "items_processed": len(lines)}
