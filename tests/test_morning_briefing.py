"""
Tests for jobs/morning_briefing_job.py — formatting logic and pipeline resilience.
All external calls (calendar, tasks, email, Telegram, LLM) are mocked.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

from jobs.morning_briefing_job import (
    _fmt_calendar, _fmt_emails, _fmt_tasks, _llm_highlight,
)


# ── Sample data fixtures ────────────────────────────────────────────────────────

def _event(title, start="2026-04-10T09:00:00", all_day=False, location=""):
    return {"id": "e1", "title": title, "start_time": start,
            "end_time": start, "all_day": all_day, "location": location}


def _email(sender, subject):
    return {"message_id": "m1", "sender": sender, "subject": subject,
            "date": "Thu, 10 Apr 2026 08:00:00 +0000", "snippet": "preview text"}


def _task(title, due=""):
    return {"id": "t1", "title": title, "due": due, "notes": "", "task_list": "My Tasks"}


# ── Calendar formatter ──────────────────────────────────────────────────────────

def test_fmt_calendar_no_events():
    result = _fmt_calendar([])
    assert "No events today" in result


def test_fmt_calendar_source_error():
    result = _fmt_calendar([], "insufficient authentication scopes")
    assert "Unavailable" in result
    assert "Google auth" in result


def test_fmt_calendar_single_event():
    result = _fmt_calendar([_event("Team standup", "2026-04-10T09:00:00")])
    assert "1 event" in result
    assert "Team standup" in result
    assert "9:00 AM" in result


def test_fmt_calendar_multiple_events():
    events = [
        _event("Standup", "2026-04-10T09:00:00"),
        _event("Client call", "2026-04-10T14:30:00"),
    ]
    result = _fmt_calendar(events)
    assert "2 events" in result
    assert "Standup" in result
    assert "Client call" in result
    assert "2:30 PM" in result


def test_fmt_calendar_all_day_event():
    result = _fmt_calendar([_event("Company holiday", all_day=True)])
    assert "all day" in result
    assert "Company holiday" in result


def test_fmt_calendar_event_with_location():
    result = _fmt_calendar([_event("Offsite", location="Conference Room A")])
    assert "Conference Room A" in result


# ── Email formatter ─────────────────────────────────────────────────────────────

def test_fmt_emails_no_emails():
    result = _fmt_emails([])
    assert "No new messages" in result


def test_fmt_emails_source_error():
    result = _fmt_emails([], "Unable to find the server at gmail.googleapis.com")
    assert "Unavailable" in result
    assert "network" in result


def test_fmt_emails_single():
    result = _fmt_emails([_email("alice@example.com", "Q1 Report")])
    assert "1 unread" in result
    assert "alice" in result
    assert "Q1 Report" in result


def test_fmt_emails_display_name_stripped():
    """'Boss Name <boss@corp.com>' should show as 'Boss Name', not the angle-bracket form."""
    result = _fmt_emails([_email('"Alice Smith" <alice@corp.com>', "Invoice")])
    assert "Alice Smith" in result
    assert "<" not in result


def test_fmt_emails_truncates_at_limit():
    emails = [_email(f"s{i}@x.com", f"Subject {i}") for i in range(10)]
    result = _fmt_emails(emails)
    assert "more" in result
    assert "Subject 0" in result
    assert "Subject 9" not in result  # beyond _MAX_EMAILS_SHOWN


# ── Tasks formatter ─────────────────────────────────────────────────────────────

def test_fmt_tasks_no_tasks():
    result = _fmt_tasks([])
    assert "Nothing pending" in result


def test_fmt_tasks_source_error():
    result = _fmt_tasks([], "insufficient authentication scopes")
    assert "Unavailable" in result
    assert "Google auth" in result


def test_fmt_tasks_single():
    result = _fmt_tasks([_task("Review contract")])
    assert "1 pending" in result
    assert "Review contract" in result


def test_fmt_tasks_with_due_date():
    result = _fmt_tasks([_task("File taxes", due="2026-04-15T00:00:00Z")])
    assert "Apr 15" in result


def test_fmt_tasks_truncates_at_limit():
    tasks = [_task(f"Task {i}") for i in range(10)]
    result = _fmt_tasks(tasks)
    assert "more" in result
    assert "Task 0" in result
    assert "Task 9" not in result  # beyond _MAX_TASKS_SHOWN


def test_fmt_tasks_invalid_due_date_does_not_crash():
    result = _fmt_tasks([_task("Bad date task", due="not-a-date")])
    assert "Bad date task" in result


# ── LLM highlight ───────────────────────────────────────────────────────────────

def test_llm_highlight_returns_empty_when_no_data():
    result = _llm_highlight([], [], [])
    assert result == ""


def test_llm_highlight_disabled_by_env(monkeypatch):
    monkeypatch.setenv("BRIEFING_LLM_SUMMARY", "0")
    import importlib
    import jobs.morning_briefing_job as m

    importlib.reload(m)
    # When disabled, _llm_highlight should return "" immediately
    result = m._llm_highlight([_event("Standup")], [], [])
    assert result == ""


def test_llm_highlight_ollama_unreachable():
    """If Ollama is down, returns '' without raising."""
    events = [_event("Big presentation")]
    result = _llm_highlight(events, [], [], )
    # With no real Ollama running in test env this should silently return ""
    assert isinstance(result, str)


# ── Full pipeline ───────────────────────────────────────────────────────────────

def test_run_briefing_sends_telegram_and_returns_text():
    import jobs.morning_briefing_job as m
    sample_events = [_event("Standup")]
    sample_emails = [_email("alice@x.com", "Report")]
    sample_tasks  = [_task("Follow up")]

    with patch.object(m, "get_todays_events_raw_status", return_value=(sample_events, "")), \
         patch.object(m, "get_recent_emails_raw_status", return_value=(sample_emails, "")), \
         patch.object(m, "get_pending_tasks_raw_status", return_value=(sample_tasks, "")), \
         patch.object(m, "_llm_highlight", return_value="Focus on the standup."), \
         patch.object(m, "send_telegram", return_value="sent ok") as mock_tg, \
         patch.object(m, "notify", return_value=None):
        result = m.run_briefing()

    mock_tg.assert_called_once()
    assert "Standup" in result
    assert "Report" in result
    assert "Follow up" in result
    assert "Focus on the standup." in result


def test_run_briefing_telegram_failure_does_not_raise():
    import jobs.morning_briefing_job as m
    with patch.object(m, "get_todays_events_raw_status", return_value=([], "")), \
         patch.object(m, "get_recent_emails_raw_status", return_value=([], "")), \
         patch.object(m, "get_pending_tasks_raw_status", return_value=([], "")), \
         patch.object(m, "_llm_highlight", return_value=""), \
         patch.object(m, "send_telegram", side_effect=Exception("bot blocked")), \
         patch.object(m, "notify", return_value=None):
        result = m.run_briefing()
    assert isinstance(result, str)


def test_run_briefing_all_sources_unavailable():
    """If all Google APIs return [], briefing still sends a coherent empty digest."""
    import jobs.morning_briefing_job as m
    with patch.object(m, "get_todays_events_raw_status", return_value=([], "")), \
         patch.object(m, "get_recent_emails_raw_status", return_value=([], "")), \
         patch.object(m, "get_pending_tasks_raw_status", return_value=([], "")), \
         patch.object(m, "_llm_highlight", return_value=""), \
         patch.object(m, "send_telegram", return_value="sent") as mock_tg, \
         patch.object(m, "notify", return_value=None):
        result = m.run_briefing()
    mock_tg.assert_called_once()
    assert "No events today" in result
    assert "No new messages" in result
    assert "Nothing pending" in result


def test_run_briefing_source_errors_are_visible():
    import jobs.morning_briefing_job as m
    with patch.object(m, "get_todays_events_raw_status", return_value=([], "insufficient authentication scopes")), \
         patch.object(m, "get_recent_emails_raw_status", return_value=([], "")), \
         patch.object(m, "get_pending_tasks_raw_status", return_value=([], "insufficient authentication scopes")), \
         patch.object(m, "_llm_highlight", return_value=""), \
         patch.object(m, "send_telegram", return_value="sent"), \
         patch.object(m, "notify", return_value=None):
        result = m.run_briefing()
    assert "CALENDAR\nUnavailable" in result
    assert "TASKS\nUnavailable" in result
    assert "No events today" not in result
    assert "Nothing pending" not in result
