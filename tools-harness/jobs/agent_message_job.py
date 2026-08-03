"""
Agent Message Job — dispatches agent-to-agent messages via _TASK_MODULES.

Dispatched by the worker when job_queue has an "agent_message" task.
Reads the message from agent_message_queue, processes it:
  orchestrator → calls harness.run() with full orchestrated tool loop
  user         → calls send_telegram() directly
  other agents → marks as queued (handled by their next poll cycle)
"""
from __future__ import annotations

import json as _json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jobs import job_queue
from jobs.agent_message_queue import get_message, mark_done as _msg_done
from log_config import setup_logging

_log = setup_logging("agent_message_job")


def run_as_job(params: dict, job_id: str) -> None:
    msg_id = params.get("message_id", "")
    if not msg_id:
        _log.warning("agent_message job %s: no message_id", job_id[:8])
        job_queue.checkpoint(job_id, "failed")
        return

    msg = get_message(msg_id)
    if not msg:
        _log.warning("agent_message job %s: message %s not found", job_id[:8], msg_id[:8])
        job_queue.checkpoint(job_id, "failed")
        return

    to = msg["to_agent"]
    priority = msg["priority"]
    _log.info("agent_message job %s: %s → %s: %r", job_id[:8], msg["from_agent"], to, msg["subject"])

    job_queue.checkpoint(job_id, "processing")

    if to in ("orchestrator", "research"):
        _handle_orchestrator(msg, job_id)
    elif to == "user":
        _handle_user(msg)
    else:
        _log.warning("agent_message %s: no handler for agent %r, dropped", msg_id[:8], to)

    _msg_done(msg_id, _json.dumps({"job_id": job_id, "to": to}))


def _handle_orchestrator(msg: dict, job_id: str) -> None:
    priority_tag = "!" * msg["priority"] if msg["priority"] else ""
    query = f"[agent message{priority_tag} from {msg['from_agent']}] {msg['subject']}\n\n{msg['body']}"

    import harness
    harness.run(
        query=query,
        intent="agent_message",
        orchestrated=True,
    )


def _handle_user(msg: dict) -> None:
    from tools.telegram_send import send_telegram
    body = f"[{msg['from_agent']}] {msg['subject']}\n{msg['body']}"
    send_telegram(body[:4000])
    _log.info("notified user via Telegram: %r", msg["subject"])
