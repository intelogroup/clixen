"""Shared alert-decision gate for background handlers.

Handlers already run autonomously (jobs/worker.py's poll loop) and judge
"is this finding worth surfacing" via their own hardcoded thresholds before
calling add_notification directly. This adds one more check on top: a cheap
LLM decision call (same chat() primitive reddit_intel already uses for merge
decisions) gets a real look at the finding and can suppress a marginal one
that technically passed the handler's own threshold.

Never makes things less reliable than today: any failure (budget exceeded,
malformed response) falls back to the handler's own pre-existing verdict.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable

_log = logging.getLogger(__name__)

# ponytail: ringback's pjsua backend crashes (native assert abort) if a second
# call is placed while the first is still tearing down its SIP registration —
# hit live when science_scout fired two strong findings ~seconds apart. One
# process-wide lock serializes calls; good enough since all callers run in
# the same worker process.
_CALL_LOCK = threading.Lock()

_DECISION_SYSTEM_PROMPT = """You decide whether a background finding is worth interrupting \
the user for. Given the finding, respond with ONLY JSON: {"alert": true|false, "reason": \
"one sentence"}. Alert only if it is actionable, time-sensitive, or high-value; otherwise false."""


def decide_and_notify(
    finding: str,
    source: str,
    *,
    fallback_alert: bool,
    level: str = "info",
    action_label: str = "",
    action_url: str = "",
    action_type: str = "",
    action_payload: dict | None = None,
    on_suppress: Callable[[str, str], None] | None = None,
    wake_agent: bool = False,
    call_phone: bool = False,
    bypass_gate: bool = False,
) -> bool:
    """Ask whether `finding` should alert the user; notify if so. Returns
    whether a notification was sent.

    wake_agent=True: instead of pushing the raw finding, hand it to the
    orchestrator (harness.run, real tool access) to independently verify —
    cross-check sources, judge credibility — before anything reaches the
    user. Falls back to the raw finding if the agent run itself fails, so a
    harness bug never silently eats an alert that passed the gate.

    call_phone=True: on top of the Telegram push, also place a real SIP call
    via ringback (Linphone) speaking the message. Best-effort — cooldown/
    docker/call failures never block the Telegram push or the notification
    row, they just log.

    bypass_gate=True: skip the LLM alert/suppress judgment entirely and use
    fallback_alert as-is. The gate's "is this actually worth interrupting
    for" call is calibrated for noisy/ambiguous sources — a caller that's
    already applied its own hard threshold (e.g. science_scout's
    Observed/Replicated evidence-level gate) doesn't need a second, looser
    LLM opinion able to veto it."""
    alert, reason = fallback_alert, ""
    if not bypass_gate:
        from clients.cloud_client import chat
        from clients.cost_guard import BudgetExceededError

        try:
            resp = chat(user_message=finding, system_prompt=_DECISION_SYSTEM_PROMPT, reasoning_effort="low")
            start, end = resp.find("{"), resp.rfind("}")
            decision = json.loads(resp[start:end + 1])
            alert = bool(decision.get("alert", fallback_alert))
            reason = decision.get("reason", "")
        except BudgetExceededError:
            _log.warning("[notify_gate] budget exceeded, falling back to fallback_alert=%s", fallback_alert)
        except (ValueError, json.JSONDecodeError):
            _log.warning("[notify_gate] malformed decision response, falling back to fallback_alert=%s", fallback_alert)

    if not alert:
        _log.info("[notify_gate] suppressed finding from %s: %s", source, reason or "(no reason)")
        if on_suppress is not None:
            on_suppress(finding, reason)
        return False

    message = f"{finding}\n\n{reason}" if reason else finding
    if wake_agent:
        message = _verify_via_agent(finding, source) or message

    from store.workflow_store import add_notification
    add_notification(
        message=message, level=level, source=source,
        action_label=action_label, action_url=action_url,
        action_type=action_type, action_payload=action_payload,
    )
    _push_telegram(message)
    if call_phone:
        _call_linphone(message)
    return True


def _verify_via_agent(finding: str, source: str) -> str:
    """Runs the finding through the real orchestrator (web search, tool
    access) so a verified/skeptical take reaches the user instead of the
    raw unverified finding. Never raises — caller falls back on ''."""
    import harness

    query = (
        f"A background job ({source}) flagged this finding for your review:\n\n{finding}\n\n"
        "Verify it — cross-check against other sources if you can, note anything shaky or "
        "overstated — then give me a short, skeptical take on whether it's actually worth "
        "my attention."
    )
    try:
        result, _model, _intent = harness.run(query=query, chat_id=None, orchestrated=True, channel="telegram")
        return result.strip()
    except Exception:
        _log.warning("[notify_gate] agent verification failed for %s, falling back to raw finding", source, exc_info=True)
        return ""


def _push_telegram(text: str) -> None:
    """Best-effort Telegram push — missing creds or send failure never
    blocks the notification row from being written."""
    import os

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        from scripts.email_watch import _send_telegram
        _send_telegram(token, chat_id, text)
    except Exception:
        _log.warning("[notify_gate] telegram push failed", exc_info=True)


def _call_linphone(text: str) -> None:
    """Best-effort SIP call via ringback — cooldown/docker/call failures
    never raise, caller already sent Telegram as the guaranteed channel."""
    try:
        from tools.connector_ringback import call_my_phone
        # Callers build messages with \n\n between finding/reason for readable Telegram
        # text — Piper renders a literal paragraph break as a long silence gap inside
        # the single WAV, which sounds like the call audio stopping and restarting.
        # Collapse to single spaces since this is spoken, not displayed.
        spoken = " ".join(text.split())
        # ponytail: 1600 chars ~= 90s spoken — enough for a real scientific-depth
        # briefing (result + numbers + methodology), not just a headline. Was 400,
        # which cut every finding down to a one-line hook regardless of content.
        with _CALL_LOCK:
            result = call_my_phone(spoken[:1600])
        if result.startswith("[ringback cooldown]"):
            _log.info("[notify_gate] %s", result)
        elif result.startswith("[ringback error]") or result.startswith("[invalid arguments]"):
            _log.warning("[notify_gate] ringback call not placed: %s", result)
    except Exception:
        _log.warning("[notify_gate] ringback call failed", exc_info=True)
