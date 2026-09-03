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
import os
from typing import Callable

_log = logging.getLogger(__name__)

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
    bypass_gate: bool = False,
) -> bool:
    """Ask whether `finding` should alert the user; notify if so. Returns
    whether a notification was sent.

    wake_agent=True: instead of pushing the raw finding, hand it to the
    orchestrator (harness.run, real tool access) to independently verify —
    cross-check sources, judge credibility — before anything reaches the
    user. Falls back to the raw finding if the agent run itself fails, so a
    harness bug never silently eats an alert that passed the gate.

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
    _push_telegram(_format_for_telegram(source, _summarize_for_telegram(message)))
    return True


def _format_for_telegram(source: str, summary: str) -> str:
    """Legacy Telegram Markdown (bold/italic only, no escaping needed) styled
    as a badge + divider + summary + footer card. Blue = scout, red = monitor."""
    src = (source or "").lower()
    if "scout" in src:
        badge, label = "🔵", "SCOUT · Science Finding"
    elif "monitor" in src:
        badge, label = "🔴", "MONITOR · World Signal"
    else:
        badge, label = "⚪️", source or "Update"
    return (
        f"{badge} *{label}*\n\n"
        f"{summary}\n\n"
        f"_Full debrief saved to dashboard_"
    )


def _summarize_for_telegram(text: str) -> str:
    """Compress a full finding/briefing into a short 2-3 sentence push. The
    full text is already saved via add_notification above — this only
    shortens what actually lands in the chat."""
    if not text:
        return text
    try:
        from clients.cloud_client import chat
        resp = chat(
            user_message=(
                "Compress this into a short, useful 2-3 sentence Telegram "
                "notification. Keep the key fact/number, drop background and "
                "caveats. Return only the summary, no preamble.\n\n" + text
            ),
            reasoning_effort="low",
        )
        return str(resp or "").strip() or text
    except Exception:
        _log.warning("[notify_gate] telegram summarize failed; using source text", exc_info=True)
        return text


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
        _send_telegram(token, chat_id, text, parse_mode="Markdown")
    except Exception:
        _log.warning("[notify_gate] telegram push failed", exc_info=True)
