"""
ask_verse_agent — Clixen's own agent identity in the aiverse "Verse" social
sim (a separate local project, ~/Developer/aiverse) acting as one more
subagent: post a question to the Verse's public room as Clixen, then let a
background job (jobs/verse_ask_poll_job.py) watch for a peer reply and
surface it via notifications.push() once one arrives.

Verse replies are not instant (native/subject agents tick on their own
30s-150s cadence) so this tool never blocks the orchestrator turn — it
returns as soon as the question is posted.

Auth: the Verse gateway issues its own agent token (its owner/agent system
is unrelated to Clixen's local auth). Set VERSE_AGENT_TOKEN once via
`python -m tools.verse_agent` (see main() below) or manually in .env.local.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)

GATEWAY = os.environ.get("VERSE_GATEWAY_URL", "http://localhost:3010")
ROOM = os.environ.get("VERSE_ROOM", "general")


def _token() -> str:
    token = os.environ.get("VERSE_AGENT_TOKEN", "")
    if not token:
        raise RuntimeError("VERSE_AGENT_TOKEN not set — auth an agent into the Verse first")
    return token


def _api(method: str, path: str, **kw) -> requests.Response:
    return requests.request(
        method,
        f"{GATEWAY}{path}",
        headers={"authorization": f"Bearer {_token()}", "content-type": "application/json"},
        timeout=15,
        **kw,
    )


def observe_verse(limit: int = 10) -> str:
    """Read the most recent messages in the Verse's public room, regardless of
    whether they're replies to anything Clixen posted. Read-only, never posts.

    Use this for "what's happening in the Verse" / "what have you seen there" —
    check_verse_replies only covers replies to Clixen's own questions, this
    covers general activity.
    """
    try:
        health = requests.get(f"{GATEWAY}/health", timeout=3)
        if not health.ok:
            return f"[error] Verse not reachable at {GATEWAY} (health check {health.status_code})"
    except requests.exceptions.RequestException:
        return f"[error] Verse not running locally at {GATEWAY}"

    join = _api("POST", f"/rooms/{ROOM}/join", json={})
    if join.status_code not in (200, 201):
        return f"[error] could not join Verse room '{ROOM}': {join.status_code} {join.text[:200]}"
    conversation_id = join.json().get("conversationId") or join.json().get("conversation_id")
    if not conversation_id:
        return f"[error] join_room response had no conversation id: {join.text[:200]}"

    res = _api("GET", f"/conversations/{conversation_id}/messages")
    if res.status_code != 200:
        return f"[error] could not read Verse conversation: {res.status_code} {res.text[:200]}"

    msgs = res.json().get("messages", [])[-limit:]
    if not msgs:
        return "The Verse room is empty — no messages yet."
    return "\n".join(f"[{m.get('senderAgentId', '?')[:8]}] {m.get('content', '')}" for m in msgs)


def check_verse_replies() -> str:
    """Read (and mark read) any pending Verse-peer-reply notifications.

    Read-only — never posts anything. Use this to answer "did anyone reply /
    check the Verse", instead of posting a fresh question via ask_verse_agent.
    """
    from store.workflow_store import list_notifications, mark_notification_read

    pending = [n for n in list_notifications(unread_only=True) if n.get("source") == "verse_agent"]
    if not pending:
        return "No new Verse activity since you last checked."
    for n in pending:
        mark_notification_read(n["id"])
    return "\n".join(f"- {n['message']}" for n in pending)


def ask_verse_agent(query: str) -> str:
    """Post `query` into the Verse's public room as Clixen and return immediately.

    A background job polls for a peer reply and pushes a notification when one
    lands (or on timeout) — this call never waits for that reply itself.
    """
    if not query.strip():
        return "[error] ask_verse_agent: query is empty"

    try:
        health = requests.get(f"{GATEWAY}/health", timeout=3)
        if not health.ok:
            return f"[error] Verse not reachable at {GATEWAY} (health check {health.status_code})"
    except requests.exceptions.RequestException:
        return f"[error] Verse not running locally at {GATEWAY}"

    join = _api("POST", f"/rooms/{ROOM}/join", json={})
    if join.status_code not in (200, 201):
        return f"[error] could not join Verse room '{ROOM}': {join.status_code} {join.text[:200]}"
    conversation_id = join.json().get("conversationId") or join.json().get("conversation_id")
    if not conversation_id:
        return f"[error] join_room response had no conversation id: {join.text[:200]}"

    posted = _api("POST", f"/conversations/{conversation_id}/messages", json={"content": query})
    if posted.status_code not in (200, 201):
        return f"[error] could not post to Verse conversation: {posted.status_code} {posted.text[:200]}"
    my_message_id = posted.json().get("message", {}).get("id")

    from jobs import job_queue

    job_queue.enqueue(
        "verse_ask_poll",
        {"conversation_id": conversation_id, "query": query, "posted_message_id": my_message_id},
    )
    return f"Asked the Verse peers in #{ROOM}: \"{query}\" — will notify you when someone replies."


VERSE_AGENT_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "ask_verse_agent",
            "description": (
                "Post a message/question to the other AI agents living in the Verse (a "
                "separate multi-agent social sim) using Clixen's own agent identity there. "
                "Use for peer opinions, crowd-sourced takes, or introducing/announcing "
                "yourself — not authoritative facts. Replies arrive asynchronously, "
                "sometimes minutes later, surfaced separately (check_verse_replies), not in "
                "this turn's answer. Write `query` in Clixen's own voice, as yourself, first "
                "person — curious, direct, genuinely engaged with what peers say, not a "
                "generic corporate-assistant tone. If a peer's own tools/work overlap with "
                "something Clixen itself has (its own tool inventory — memory, automations, "
                "web/document/vision/messaging agents, world_monitor, etc.), it's fine and "
                "natural to say so ('oh yeah, I use that too')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The message/question to post to Verse peers, in Clixen's own voice.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_verse_replies",
            "description": (
                "Read-only: check whether any Verse peer has replied since you last checked "
                "— use this for 'did anyone respond' / 'check the Verse', instead of posting "
                "a new message with ask_verse_agent. Never posts anything."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "observe_verse",
            "description": (
                "Read-only: read the most recent general activity in the Verse room, "
                "regardless of whether it's a reply to anything Clixen said. Use this for "
                "'what's happening in the Verse' / 'what have you seen there' — do NOT use "
                "query_subagent_findings or any other Clixen-internal-job tool for this, "
                "those are unrelated to the Verse. Never posts anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "How many recent messages to read (default 10).",
                    },
                },
            },
        },
    },
]

VERSE_AGENT_EXECUTORS = {
    "ask_verse_agent": lambda args: ask_verse_agent(args.get("query", "")),
    "check_verse_replies": lambda args: check_verse_replies(),
    "observe_verse": lambda args: observe_verse(args.get("limit", 10)),
}
