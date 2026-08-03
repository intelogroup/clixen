"""
Regression baseline for the BCH Assignment Auto-Responder gate chain
(jobs/handlers/user_automation.py, action_type == 'imessage', watch_contact
branch). This is a LIVE, actively-polling production automation that
auto-accepts/declines real paid interpreter assignments and creates real
calendar events — there was no repo-committed test coverage for it before
this file (only ad-hoc scratchpad testing during the calendar-wiring work
on 2026-07-16, never checked in). Written BEFORE refactoring gates 1/4/5
onto jobs/gate_rules.py, specifically so a behavior change is caught by a
failing test instead of discovered live against a real assignment.

Monkeypatches real module attributes (tools.imessage_search.send/
list_new_from, tools.gcalendar.check_calendar_conflict/create_calendar_event,
store.workflow_store.update_workflow_instance) — never sys.modules
replacement, per the established gotcha (tools/registry.py imports symbols
from tools.imessage_search at load time).
"""
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import lancedb  # pre-import before any monkeypatch replaces threading.Thread
import ollama
import tools.imessage_search as im_mod
import tools.gcalendar as gcal_mod
import store.workflow_store as wf_mod
from jobs.handlers import user_automation as ua

BASE_CONFIG = {
    "watch_contact": "+17817293736",
    "trigger_phrases": ["can you help", "wondering if you can help", "enter yes or no"],
    "blackout_days": ["monday", "wednesday"],
    "skip_keywords": ["today", "tomorrow"],
    "blacklist_locations": ["burlington"],
    "earliest_start_hour": 8,
    "reply_message": "Yes I can!",
    "seen_rowid": 0,
    "check_calendar": True,
    "calendar_buffer_minutes": 60,
}


def _future_mmdd(days_ahead=10):
    # skip Monday/Wednesday (BASE_CONFIG's blackout_days) so this helper
    # stays date-independent — it previously landed on a blackout weekday
    # depending on what "today" was, failing the accept-path test for
    # reasons unrelated to the code under test.
    d = datetime.now() + timedelta(days=days_ahead)
    while d.strftime("%A").lower() in ("monday", "wednesday"):
        d += timedelta(days=1)
    return d.strftime("%-m/%d")


def _next_weekday_mmdd(weekday_name: str):
    d = datetime.now()
    while d.strftime("%A").lower() != weekday_name:
        d += timedelta(days=1)
    return d.strftime("%-m/%d")


def _make_instance(config, messages):
    return {
        "id": "test-bch",
        "action_type": "imessage",
        "task_name": "BCH Assignment Auto-Responder",
        "config": dict(config),
    }, messages


class _SyncThread:
    """Stand-in for threading.Thread that runs its target synchronously,
    inline, instead of on a real background thread with a real sleep — the
    handler now queues replies with a random 60-180s delay off-thread (see
    ponytail comment on _queue_send) so a real test run can't wait for it.
    Combined with random.uniform patched to 0 below, this makes the delayed
    send deterministic and immediate for assertions."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


def _mock_ollama_chat(*a, **kw):
    """Mock ollama.Client().chat — return empty raw_date so handler falls back
    to parsing the raw message text directly (line 487), avoiding fragile
    regex extraction of dates from the prompt which includes now_line()."""
    return {"message": {"content": json.dumps({"assignments": [{"location": "Newton", "raw_date": "", "raw_time": ""}]})}}


def _run(monkeypatch, config, messages):
    # Caller must monkeypatch gcal_mod.check_calendar_conflict itself before
    # calling this — the desired conflict/no-conflict behavior differs per
    # test case, so there's no sensible shared default to set here.
    sent = []
    cal_calls = []
    updates = []

    def _send(to, message):
        sent.append((to, message))
        return "sent"

    def _list_new_from(contact, since_rowid=0, limit=20):
        return messages

    def _create_event(title, start_iso, end_iso="", description="", location=""):
        cal_calls.append({"title": title, "start_iso": start_iso, "end_iso": end_iso, "location": location})
        return f"Event created: '{title}' on {start_iso}"

    def _update(instance_id, config=None):
        updates.append(config)

    monkeypatch.setattr(im_mod, "send", _send)
    monkeypatch.setattr(im_mod, "list_new_from", _list_new_from)
    monkeypatch.setattr(gcal_mod, "create_calendar_event", _create_event)
    monkeypatch.setattr(wf_mod, "update_workflow_instance", _update)
    monkeypatch.setattr(ua.threading, "Thread", _SyncThread)
    monkeypatch.setattr(ua.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(ua.time, "sleep", lambda s: None)
    monkeypatch.setattr(ollama.Client, "chat", _mock_ollama_chat)

    instance, _ = _make_instance(config, messages)
    result = ua.handle(instance)
    return result, sent, cal_calls, updates


def test_accepts_clean_assignment_and_creates_calendar_event(monkeypatch):
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)
    mmdd = _future_mmdd()
    messages = [{"rowid": 1, "text": f"Can you help {mmdd} 9:00am-11:00am at Newton, please confirm"}]
    result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
    assert result["success"] is True
    assert len(sent) == 1
    assert "Yes I can!" in sent[0][1]
    assert len(cal_calls) == 1
    assert cal_calls[0]["location"] == "Newton"


def test_blacklisted_location_declines_no_calendar_event(monkeypatch):
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)
    mmdd = _future_mmdd()
    messages = [{"rowid": 1, "text": f"Can you help {mmdd} 9am-11am at Burlington, please confirm"}]
    result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
    assert len(cal_calls) == 0
    assert len(sent) == 0


def test_blackout_weekday_declines(monkeypatch):
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)
    mmdd_mon = _next_weekday_mmdd("monday")
    messages = [{"rowid": 1, "text": f"Can you help {mmdd_mon} 9am-11am at Newton, please confirm"}]
    result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
    assert len(cal_calls) == 0
    assert len(sent) == 0


def test_earliest_hour_gate_declines(monkeypatch):
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)
    mmdd = _future_mmdd()
    messages = [{"rowid": 1, "text": f"Can you help {mmdd} 6am-8am at Newton, please confirm"}]
    result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
    assert len(cal_calls) == 0
    assert len(sent) == 0


def test_calendar_conflict_declines(monkeypatch):
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: "Existing Event at 09:00")
    mmdd = _future_mmdd()
    messages = [{"rowid": 1, "text": f"Can you help {mmdd} 9am-11am at Newton, please confirm"}]
    result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
    assert len(cal_calls) == 0
    assert len(sent) == 0


def test_skip_keyword_declines(monkeypatch):
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)
    mmdd = _future_mmdd()
    messages = [{"rowid": 1, "text": f"Can you help {mmdd} 9am-11am at Newton TODAY, please confirm"}]
    result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
    assert len(cal_calls) == 0
    assert len(sent) == 0


def test_gate_order_blackout_before_earliest_hour(monkeypatch):
    # An assignment that fails BOTH the blackout-day gate AND the
    # earliest-hour gate must decline with the blackout-day reason (gate 1
    # runs before gate 4) — pins the exact ordering so a refactor can't
    # silently reorder gates even if every individual gate still "works".
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)
    mmdd_mon = _next_weekday_mmdd("monday")
    messages = [{"rowid": 1, "text": f"Can you help {mmdd_mon} 6am-8am at Newton, please confirm"}]
    result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
    assert len(cal_calls) == 0
    assert "falls on monday" in result["log"][0].lower()
    assert "starts" not in result["log"][0].lower()  # must NOT be the earliest-hour reason


def test_seen_rowid_advances_past_accepted_message(monkeypatch):
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)
    mmdd = _future_mmdd()
    messages = [{"rowid": 42, "text": f"Can you help {mmdd} 9am-11am at Newton, please confirm"}]
    result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
    assert updates[-1]["seen_rowid"] == 42


def test_calendar_conflict_passes_assignment_end_not_just_start(monkeypatch):
    # Regression for the live bug (2026-07-17): a 10:40AM-1:40PM assignment
    # was auto-accepted despite directly overlapping a 12:50PM-4:38PM event,
    # because the old call only checked a window around the assignment's
    # START time. Assert the handler now passes `end` through to
    # check_calendar_conflict so the callee can see the full span.
    captured = {}

    def _check(at, buffer_minutes=60, end=None):
        captured["at"] = at
        captured["end"] = end
        return None

    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", _check)
    mmdd = _future_mmdd()
    messages = [{"rowid": 1, "text": f"Can you help {mmdd} 10:40am-1:40pm at Newton, please confirm"}]
    _run(monkeypatch, BASE_CONFIG, messages)
    assert captured["at"].hour == 10 and captured["at"].minute == 40
    assert captured["end"] is not None
    assert captured["end"].hour == 13 and captured["end"].minute == 40


def test_calendar_error_is_treated_as_conflict_not_accepted(monkeypatch):
    # Fail-safe: an unverifiable calendar ("[calendar error] ...") must
    # decline, never silently auto-accept just because it isn't a real
    # confirmed conflict.
    monkeypatch.setattr(
        gcal_mod, "check_calendar_conflict",
        lambda at, buffer_minutes=60, end=None: "[calendar error] Not authenticated.",
    )
    mmdd = _future_mmdd()
    messages = [{"rowid": 1, "text": f"Can you help {mmdd} 9am-11am at Newton, please confirm"}]
    result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
    assert len(cal_calls) == 0
    assert len(sent) == 0


def test_send_failure_does_not_retry_or_hold_back_rowid(monkeypatch):
    # Regression for the live duplicate-SMS-spam bug (2026-07-17): a send
    # failure must NOT hold seen_rowid back for a retry — that behavior was
    # itself re-firing real sends every poll. seen_rowid must always advance
    # past every message in the batch, failure or not.
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)

    def _send_fail(to, message):
        return "[imessage_send error] iMessage failed and SMS fallback also not confirmed in DB."

    monkeypatch.setattr(im_mod, "send", _send_fail)
    mmdd = _future_mmdd()
    messages = [{"rowid": 99, "text": f"Can you help {mmdd} 9am-11am at Newton, please confirm"}]

    cal_calls = []
    updates = []
    monkeypatch.setattr(im_mod, "list_new_from", lambda contact, since_rowid=0, limit=20: messages)
    monkeypatch.setattr(gcal_mod, "create_calendar_event",
                         lambda title, start_iso, end_iso="", description="", location="": cal_calls.append(1))
    monkeypatch.setattr(wf_mod, "update_workflow_instance",
                         lambda instance_id, config=None: updates.append(config))
    monkeypatch.setattr(ua.threading, "Thread", _SyncThread)
    monkeypatch.setattr(ua.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(ua.time, "sleep", lambda s: None)

    instance, _ = _make_instance(BASE_CONFIG, messages)
    result = ua.handle(instance)

    assert updates[-1]["seen_rowid"] == 99  # advanced past the failed message, not held back
    assert result["success"] is True  # no retry means no "will retry" failure state either


def test_reply_is_queued_with_60_to_180s_random_delay(monkeypatch):
    # An instant auto-reply reads as a bot; the handler must queue the real
    # send with a random 60-180s delay rather than sending synchronously.
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)
    captured_delays = []
    real_uniform = random.uniform

    def _spy_uniform(a, b):
        d = real_uniform(a, b)
        captured_delays.append((a, b, d))
        return d

    monkeypatch.setattr(ua.random, "uniform", _spy_uniform)
    monkeypatch.setattr(ua.threading, "Thread", _SyncThread)
    monkeypatch.setattr(ua.time, "sleep", lambda s: None)
    monkeypatch.setattr(ollama.Client, "chat", _mock_ollama_chat)
    mmdd = _future_mmdd()
    messages = [{"rowid": 1, "text": f"Can you help {mmdd} 9am-11am at Newton, please confirm"}]

    sent = []
    monkeypatch.setattr(im_mod, "send", lambda to, message: sent.append((to, message)) or "sent")
    monkeypatch.setattr(im_mod, "list_new_from", lambda contact, since_rowid=0, limit=20: messages)
    monkeypatch.setattr(gcal_mod, "create_calendar_event",
                         lambda title, start_iso, end_iso="", description="", location="": "Event created")
    monkeypatch.setattr(wf_mod, "update_workflow_instance", lambda instance_id, config=None: None)

    instance, _ = _make_instance(BASE_CONFIG, messages)
    ua.handle(instance)

    assert len(captured_delays) == 1
    lo, hi, delay = captured_delays[0]
    assert lo == 60 and hi == 180
    assert 60 <= delay <= 180
    assert len(sent) == 1  # _SyncThread still runs the queued send inline


def test_no_help_request_phrase_skips_entirely(monkeypatch):
    # A message with a date/time/location but no "can you help" ask (e.g. a
    # case-gone notice) must not be extracted or replied to at all.
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)
    messages = [{"rowid": 1, "text": "Hi Kalinov, sorry 07/22 at MEEI is gone. Thanks- Karla"}]
    result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
    assert len(sent) == 0
    assert len(cal_calls) == 0
    assert updates[-1]["seen_rowid"] == 1


def test_help_request_phrase_variants_still_processed(monkeypatch):
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)
    mmdd = _future_mmdd()
    for phrase in ("Can you help", "can you help", "Wondering if you can help", "enter yes or no"):
        messages = [{"rowid": 1, "text": f"Hi Kalinov, {phrase} on {mmdd} 9am-11am at Newton? Thanks"}]
        result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
        assert len(sent) == 1, f"phrase {phrase!r} should have triggered a reply"


def test_decline_does_not_send(monkeypatch):
    monkeypatch.setattr(gcal_mod, "check_calendar_conflict", lambda at, buffer_minutes=60, end=None: None)
    mmdd_mon = _next_weekday_mmdd("monday")
    messages = [{"rowid": 1, "text": f"Can you help {mmdd_mon} 9am-11am at Newton, please confirm"}]
    result, sent, cal_calls, updates = _run(monkeypatch, BASE_CONFIG, messages)
    assert len(sent) == 0
    assert len(cal_calls) == 0
