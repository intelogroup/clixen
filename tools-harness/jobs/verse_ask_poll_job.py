"""
Verse Ask Poll Job — waits for a peer reply to a question ask_verse_agent()
posted into the aiverse Verse, then surfaces it via store.workflow_store's
persisted, cross-process notification table.

Dispatched by the worker when job_queue has a "verse_ask_poll" task
(params: conversation_id, query, posted_message_id).

Verse peers (native/subject harnesses) don't reliably react to an injected
message — confirmed live 2026-09-02: two OCR questions posted into an
ongoing governance thread got zero on-topic replies, just continuations of
the peers' existing topic. A naive "any new message = the reply" check would
report that noise as an answer. _is_relevant() gates on a cheap LLM judge
before accepting a message as a real reply; irrelevant messages are skipped
and polling continues until a real answer or the timeout.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jobs import job_queue
from log_config import setup_logging
from store.workflow_store import add_notification
from tools.verse_agent import _api

_log = setup_logging("verse_ask_poll_job")

_POLL_SECONDS = 20
_MAX_ATTEMPTS = 45  # ~15 minutes

_RELEVANCE_PROMPT = (
    "You judge whether a chat message actually answers or directly addresses a "
    "question, versus just continuing an unrelated conversation. Respond with "
    'ONLY JSON: {"relevant": true|false}.'
)


def _is_relevant(query: str, message: str) -> bool:
    """Cheap LLM gate. Any failure (budget, malformed JSON) falls back to False —
    the timeout path still fires eventually, so this never hangs the job."""
    try:
        from clients.cloud_client import chat

        resp = chat(
            user_message=f"Question: {query}\n\nCandidate message: {message}",
            system_prompt=_RELEVANCE_PROMPT,
            reasoning_effort="low",
        )
        start, end = resp.find("{"), resp.rfind("}")
        return bool(json.loads(resp[start : end + 1]).get("relevant", False))
    except Exception as e:
        _log.warning("verse_ask_poll relevance judge failed: %s", e)
        return False


def run_as_job(params: dict, job_id: str, cancel_event: threading.Event | None = None) -> None:
    """cancel_event: set by worker.py once its own dispatch timeout fires — it
    stops WAITING on this thread but doesn't kill it, so this loop must check
    the event itself (via the interruptible wait() below) to actually stop.
    Without this, the thread ran to its own natural completion regardless,
    long after job_queue already recorded the job as failed — confirmed live
    2026-09-02 as a duplicate "no reply" notification ~16 min after timeout,
    while also blocking the worker's single-threaded dispatch loop from
    claiming other queued jobs for that whole span."""
    conversation_id = params.get("conversation_id", "")
    query = params.get("query", "")
    posted_message_id = params.get("posted_message_id", "")
    if not conversation_id:
        _log.warning("verse_ask_poll job %s: no conversation_id", job_id[:8])
        job_queue.checkpoint(job_id, "failed")
        return

    job_queue.checkpoint(job_id, "polling")
    seen_own_message = not posted_message_id  # if we never got our own id, don't wait for it
    judged_ids: set[str] = set()

    for attempt in range(_MAX_ATTEMPTS):
        if cancel_event is not None:
            if cancel_event.wait(_POLL_SECONDS):
                _log.info("verse_ask_poll job %s: cancelled by dispatch timeout, stopping", job_id[:8])
                return
        else:
            time.sleep(_POLL_SECONDS)
        try:
            res = _api("GET", f"/conversations/{conversation_id}/messages")
        except Exception as e:
            _log.warning("verse_ask_poll job %s: poll failed: %s", job_id[:8], e)
            continue
        if res.status_code != 200:
            continue

        msgs = res.json().get("messages", [])
        for m in msgs:
            if cancel_event is not None and cancel_event.is_set():
                _log.info("verse_ask_poll job %s: cancelled mid-batch, stopping", job_id[:8])
                return
            if not seen_own_message:
                if m.get("id") == posted_message_id:
                    seen_own_message = True
                continue
            mid = m.get("id")
            if mid in judged_ids:
                continue
            judged_ids.add(mid)
            if not _is_relevant(query, m.get("content", "")):
                continue
            add_notification(
                message=f"Verse peer replied to \"{query}\": {m.get('content', '')}",
                source="verse_agent",
            )
            job_queue.mark_done(job_id)
            return

    add_notification(message=f"No Verse peer replied to \"{query}\" after 15 minutes.", source="verse_agent")
    job_queue.mark_done(job_id)
