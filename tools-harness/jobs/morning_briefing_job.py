"""
Morning Briefing Job
====================
Sends a daily digest to Telegram at startup (or on demand).
Includes: today's calendar events, recent unread emails, pending tasks.

Configuration (via .env):
    WATCHED_SENDERS       Same list used by inbox_monitor — comma-separated.
    BRIEFING_HOUR         Hour to run (24h format). Default: 8. Used by launchd plist.
    BRIEFING_LLM_SUMMARY  "1" to append a one-liner LLM highlight. Default: "1".

Run manually:
    cd tools-harness
    python -m jobs.morning_briefing_job
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.gcalendar import get_todays_events_raw_status
from tools.gtasks import get_pending_tasks_raw_status
from tools.gmail import get_recent_emails_raw_status
from tools.telegram_send import send_telegram
from tools.notifications import push as notify
from jobs import job_queue
# ── Config ─────────────────────────────────────────────────────────────────────

_MAX_TASKS_SHOWN  = 5
_MAX_EMAILS_SHOWN = 5
_LLM_SUMMARY      = os.environ.get("BRIEFING_LLM_SUMMARY", "1") == "1"
from clients.cloud_client import DEFAULT_CLOUD_MODEL
OLLAMA_URL        = "http://localhost:11434/api/chat"
SUMMARY_MODEL     = os.environ.get("BRIEFING_MODEL", DEFAULT_CLOUD_MODEL)


def _load_watched_senders() -> list[str]:
    raw = os.environ.get("WATCHED_SENDERS", "").strip()
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


# ── Formatters ─────────────────────────────────────────────────────────────────

def _source_error(source: str, error: str) -> str:
    if not error:
        return ""
    low = error.lower()
    if "insufficient" in low and "scope" in low:
        return (
            "Unavailable: Google auth is missing required scopes. "
            "Re-run: python tools/google_auth.py --auth"
        )
    if "not authenticated" in low:
        return f"Unavailable: {error}"
    if "unable to find the server" in low or "name or service not known" in low:
        return "Unavailable: Google API network error."
    short = " ".join(error.split())
    if len(short) > 180:
        short = short[:177] + "..."
    return f"Unavailable: {source} check failed: {short}"

def _fmt_time(iso: str, all_day: bool = False) -> str:
    if all_day:
        return "all day"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return iso


def _fmt_calendar(events: list[dict], error: str = "") -> str:
    if error:
        return "CALENDAR\n" + _source_error("Calendar", error)
    if not events:
        return "CALENDAR\nNo events today."
    lines = [f"CALENDAR — {len(events)} event{'s' if len(events) != 1 else ''} today"]
    for e in events:
        time_str = _fmt_time(e["start_time"], e.get("all_day", False))
        loc = f"  ({e['location']})" if e.get("location") else ""
        lines.append(f"  {time_str}  {e['title']}{loc}")
    return "\n".join(lines)


def _fmt_emails(emails: list[dict], error: str = "") -> str:
    if error:
        return "EMAILS\n" + _source_error("Gmail", error)
    if not emails:
        return "EMAILS\nNo new messages from watched senders."
    shown = emails[:_MAX_EMAILS_SHOWN]
    more  = len(emails) - len(shown)
    lines = [f"EMAILS — {len(emails)} unread"]
    for m in shown:
        sender = m["sender"].split("<")[0].strip().strip('"') or m["sender"]
        lines.append(f"  {sender} · {m['subject']}")
    if more:
        lines.append(f"  ...and {more} more")
    return "\n".join(lines)


def _fmt_tasks(tasks: list[dict], error: str = "") -> str:
    if error:
        return "TASKS\n" + _source_error("Tasks", error)
    if not tasks:
        return "TASKS\nNothing pending."
    shown = tasks[:_MAX_TASKS_SHOWN]
    more  = len(tasks) - len(shown)
    lines = [f"TASKS — {len(tasks)} pending"]
    for t in shown:
        due = ""
        if t.get("due"):
            try:
                due = " (due " + datetime.fromisoformat(t["due"].replace("Z", "+00:00")).strftime("%b %d") + ")"
            except Exception:
                pass
        lines.append(f"  • {t['title']}{due}")
    if more:
        lines.append(f"  ...and {more} more")
    return "\n".join(lines)


# ── LLM highlight ───────────────────────────────────────────────────────────────

def _llm_highlight(events: list[dict], emails: list[dict], tasks: list[dict]) -> str:
    """
    Ask the LLM for a single sentence highlighting the most important thing today.
    Returns "" if Ollama is unreachable or disabled.
    """
    if not _LLM_SUMMARY:
        return ""

    context_parts = []
    if events:
        context_parts.append("Calendar: " + "; ".join(e["title"] for e in events[:5]))
    if emails:
        context_parts.append("Emails: " + "; ".join(m["subject"] for m in emails[:5]))
    if tasks:
        due_soon = [t for t in tasks if t.get("due")][:3]
        if due_soon:
            context_parts.append("Due soon: " + "; ".join(t["title"] for t in due_soon))

    if not context_parts:
        return ""

    prompt = (
        "Given this person's day: " + " | ".join(context_parts) + "\n"
        "Write ONE short sentence (under 20 words) highlighting the most important thing to focus on today. "
        "Be specific, no filler words."
    )
    # Cloud-first (2026-07-31): cloud default model with built-in OpenAI fallback;
    # local ollama kept as last resort only (gemma4:12b-mlx isn't reliably loaded).
    try:
        from clients.cloud_client import DEFAULT_CLOUD_MODEL, raw_completion
        choice = raw_completion(
            model=DEFAULT_CLOUD_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            temperature=0.5,
            bypass_budget=True,
        )
        text = (choice.message.content or "").strip()
        if text:
            return text
    except Exception:
        pass
    try:
        import requests
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": SUMMARY_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": 60, "temperature": 0.5},
            },
            timeout=15,
        )
        if resp.ok:
            return resp.json()["message"]["content"].strip()
    except Exception:
        pass
    return ""


# ── Main pipeline ───────────────────────────────────────────────────────────────

def run_briefing() -> str:
    """
    Collect data, format briefing, send to Telegram (avoiding duplicates).
    Returns the full briefing text (useful for testing).
    """
    local_now = datetime.now().astimezone()
    today = local_now.strftime("%A, %b %d")
    timezone_label = local_now.strftime("%Z")
    print(f"[briefing] collecting data for {today}")

    events, calendar_error = get_todays_events_raw_status()
    senders = _load_watched_senders()
    emails, email_error = get_recent_emails_raw_status(senders, hours=24)
    tasks, tasks_error = get_pending_tasks_raw_status()

    unavailable = sum(bool(e) for e in (calendar_error, email_error, tasks_error))
    print(
        f"[briefing] {len(events)} events, {len(emails)} emails, "
        f"{len(tasks)} tasks, {unavailable} unavailable sources"
    )

    highlight = _llm_highlight(events, emails, tasks)

    sections = [
        f"Good morning! Briefing for {today} ({timezone_label})",
        "",
        _fmt_calendar(events, calendar_error),
        "",
        _fmt_emails(emails, email_error),
        "",
        _fmt_tasks(tasks, tasks_error),
    ]
    if highlight:
        sections += ["", f"Focus: {highlight}"]

    briefing = "\n".join(sections)

    try:
        result = send_telegram(briefing)
        print(f"[briefing] sent — {result}")
        notify(
            message=f"Morning briefing sent for {today}.",
            level="info",
            source="morning_briefing",
        )
    except Exception as e:
        print(f"[briefing] telegram error: {e}")
        notify(
            message=f"Morning briefing failed to send: {e}",
            level="error",
            source="morning_briefing",
        )

    return briefing


def run_as_job(params: dict, job_id: str) -> None:
    """Entry point called by the worker."""
    job_queue.init()
    job_queue.checkpoint(job_id, "started")
    run_briefing()


if __name__ == "__main__":
    result = run_briefing()
    print("\n--- BRIEFING PREVIEW ---")
    print(result)
