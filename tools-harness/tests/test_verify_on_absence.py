"""Verify-on-absence: absence claims with thin source coverage trigger one retry."""
import pytest

import harness
from harness import _ABSENCE_RE, _COMMITMENT_SOURCES, _absence_unchecked_sources, _needs_claim_check


@pytest.mark.parametrize("text", [
    "You don't have any CCCS assignments today.",
    "There is nothing scheduled for tomorrow.",
    "I couldn't find any bookings this week.",
    "No events found for that date.",
    "You do not have any meetings today.",
])
def test_absence_regex_positives(text):
    assert _ABSENCE_RE.search(text)


@pytest.mark.parametrize("text", [
    "You have 3 emails this morning.",
    "No problem, done!",
    "Your booking is at 8:25 AM today.",
    "I found the assignment — it's due Friday.",
])
def test_absence_regex_negatives(text):
    assert not _ABSENCE_RE.search(text)


def _stub_trace(monkeypatch, tools):
    from store import trace_store
    monkeypatch.setattr(trace_store, "get_trace", lambda rid: [{"tool": t} for t in tools])


def test_no_retry_when_two_sources_checked(monkeypatch):
    """messaging is now a required source (2026-08-01 fix) — must be present
    alongside one other for the >=2 threshold to pass."""
    _stub_trace(monkeypatch, ["ask_messaging_agent", "ask_calendar_agent"])
    assert _absence_unchecked_sources("Nothing scheduled today.", "Do I have anything today?", "rid") == set()


def test_no_retry_without_messaging_even_with_three_other_sources(monkeypatch):
    """2026-08-01 live bug: orchestrator called email+calendar+tasks (3 sources,
    old >=2 count passed) but never touched messaging. Must still retry."""
    _stub_trace(monkeypatch, ["ask_email_agent", "ask_calendar_agent", "ask_tasks_agent"])
    missing = _absence_unchecked_sources("Nothing found.", "Do I have anything today?", "rid")
    assert missing == {"ask_messaging_agent"}


def test_retry_lists_missing_sources(monkeypatch):
    _stub_trace(monkeypatch, ["ask_calendar_agent"])
    missing = _absence_unchecked_sources("Nothing scheduled today.", "Do I have anything today?", "rid")
    assert missing == _COMMITMENT_SOURCES - {"ask_calendar_agent"}


def test_no_retry_for_non_absence_answer(monkeypatch):
    _stub_trace(monkeypatch, [])
    assert _absence_unchecked_sources("Your booking is at 8:25 AM.", "what time is my flight", "rid") == set()


def test_retry_for_confident_answer_with_thin_coverage(monkeypatch):
    """Generalization: commitment-shaped query + confident (non-absence) answer + <2 sources still retries."""
    _stub_trace(monkeypatch, ["ask_calendar_agent"])
    missing = _absence_unchecked_sources(
        "You have a dentist appointment at 3pm.", "Do I have anything due today?", "rid"
    )
    assert missing == _COMMITMENT_SOURCES - {"ask_calendar_agent"}


def test_intent_signal_catches_commitment_question_missed_by_regex(monkeypatch):
    """2026-08-01 live failure: 'verify email and iMessage for confirmed assignments'
    doesn't match _COMMITMENT_QUESTION_RE's fixed phrase shape. Live-verified via
    clients.router.classify_message() that this exact query classifies intent=email
    (not the dead 'messaging' intent, which the classifier never actually returns —
    confirmed by grepping clients/router.py's _KNOWN_INTENTS). intent=email + cue
    words ('verify', 'confirmed', 'this month') must still trigger the fan-out check."""
    _stub_trace(monkeypatch, ["ask_email_agent"])
    query = "Verify email and iMessage for any confirmed assignments coming with CCCS this month"
    assert not harness._COMMITMENT_QUESTION_RE.search(query)  # confirms this is the regex-miss case
    missing = _absence_unchecked_sources("Nothing found.", query, "rid", intent="email")
    assert missing == _COMMITMENT_SOURCES - {"ask_email_agent"}
    assert _needs_claim_check(query, "rid", intent="email")


@pytest.mark.parametrize("intent", ["calendar", "tasks", "reminder", "email", "imessage"])
def test_intent_alone_does_not_overfire_on_plain_commands(monkeypatch, intent):
    """Over-fire guard: intent in the commitment set must NOT trigger on a plain
    send/search command with no verification cue words — only 'send an email to
    Bob' style commands, not questions about what's scheduled/due/confirmed."""
    _stub_trace(monkeypatch, [])
    query = "Send an email to Bob about the meeting notes"
    assert _absence_unchecked_sources("Sent.", query, "rid", intent=intent) == set()
    assert _needs_claim_check(query, "rid", intent=intent) == []


def test_cue_word_without_commitment_intent_does_not_fire(monkeypatch):
    """Cue words alone (no commitment intent) must not trigger — e.g. a 'today'/'check'
    hit under intent='factual_qa' is not a commitment question."""
    _stub_trace(monkeypatch, [])
    query = "Can you check today's weather forecast?"
    assert _absence_unchecked_sources("Sunny.", query, "rid", intent="factual_qa") == set()
    assert _needs_claim_check(query, "rid", intent="factual_qa") == []


def test_empty_query_and_intent_do_not_fire(monkeypatch):
    _stub_trace(monkeypatch, [])
    assert _absence_unchecked_sources("Sent.", "", "rid", intent="") == set()
    assert _absence_unchecked_sources("Sent.", None, "rid", intent="calendar") == set()
    assert _needs_claim_check("", "rid", intent="calendar") == []


def test_cue_match_is_case_insensitive(monkeypatch):
    _stub_trace(monkeypatch, ["ask_calendar_agent"])
    query = "ANY confirmed events THIS WEEK for CCCS?"
    missing = _absence_unchecked_sources("Nope.", query, "rid", intent="calendar")
    assert missing == _COMMITMENT_SOURCES - {"ask_calendar_agent"}


def test_cue_regex_respects_word_boundaries(monkeypatch):
    """'anything' substring inside another word must not false-positive the cue,
    and cue words must match on real word boundaries only."""
    _stub_trace(monkeypatch, [])
    query = "Send the verification code to Bob via email"  # 'verification' contains 'verify'? no - distinct word check
    assert _absence_unchecked_sources("Sent.", query, "rid", intent="email") == set()


def test_exactly_two_sources_is_the_no_retry_boundary(monkeypatch):
    """len(called) >= 2 is a >= not > — exactly 2 (incl. messaging) must satisfy."""
    _stub_trace(monkeypatch, ["ask_messaging_agent", "ask_calendar_agent"])
    assert _absence_unchecked_sources("Nothing found.", "Do I have anything today?", "rid") == set()


def test_three_sources_also_satisfies(monkeypatch):
    _stub_trace(monkeypatch, ["ask_email_agent", "ask_calendar_agent", "ask_messaging_agent"])
    assert _absence_unchecked_sources("Nothing found.", "Do I have anything today?", "rid") == set()


def test_one_source_still_retries_with_correct_missing_set(monkeypatch):
    _stub_trace(monkeypatch, ["ask_messaging_agent"])
    missing = _absence_unchecked_sources("Nothing found.", "Do I have anything today?", "rid")
    assert missing == _COMMITMENT_SOURCES - {"ask_messaging_agent"}


def test_claim_check_filters_out_toolless_trace_entries(monkeypatch):
    """_needs_claim_check should drop trace rows with no 'tool' key (e.g. non-tool events)."""
    from store import trace_store
    monkeypatch.setattr(
        trace_store, "get_trace",
        lambda rid: [{"tool": "ask_calendar_agent"}, {"note": "no tool key here"}, {"tool": None}],
    )
    result = _needs_claim_check("Do I have anything today?", "rid")
    assert result == [{"tool": "ask_calendar_agent"}]


def test_trace_store_exception_fails_safe_to_no_retry(monkeypatch):
    """If trace_store blows up, both gates must fail safe (no retry / no claim-check),
    not raise, since this runs in the hot response path."""
    from store import trace_store

    def boom(rid):
        raise RuntimeError("db locked")

    monkeypatch.setattr(trace_store, "get_trace", boom)
    assert _absence_unchecked_sources("Nothing found.", "Do I have anything today?", "rid") == set()
    assert _needs_claim_check("Do I have anything today?", "rid") == []


@pytest.mark.parametrize("intent", ["whatsapp_search", "slack", "temporal"])
def test_commitment_intents_cover_full_classifier_range(monkeypatch, intent):
    """2026-08-01 live check against clients.router's classify_message(): real
    commitment questions can land on whatsapp_search/slack/temporal, not just
    calendar/tasks/reminder/email/imessage. E.g. 'Check slack for confirmed
    meeting today' -> intent=slack; 'Is there anything coming up this week'
    -> intent=temporal. Must trigger same as the core intents."""
    _stub_trace(monkeypatch, [])
    query = "Anything confirmed for this week?"
    missing = _absence_unchecked_sources("Nothing found.", query, "rid", intent=intent)
    assert missing == _COMMITMENT_SOURCES


def test_temporal_intent_does_not_overfire_on_plain_time_query(monkeypatch):
    """'temporal' is broad — must not fire without a cue word ('what time is it')."""
    _stub_trace(monkeypatch, [])
    assert _absence_unchecked_sources("It's 3pm.", "What time is it right now", "rid", intent="temporal") == set()


def test_both_answer_and_query_triggers_still_single_coherent_result(monkeypatch):
    """Answer matches _ABSENCE_RE AND query is commitment-shaped simultaneously —
    the OR must not double-count or diverge, same missing-set either way."""
    _stub_trace(monkeypatch, ["ask_calendar_agent"])
    missing = _absence_unchecked_sources(
        "Nothing scheduled today.", "Do I have anything due today?", "rid", intent="calendar"
    )
    assert missing == _COMMITMENT_SOURCES - {"ask_calendar_agent"}


def test_extra_unrelated_tools_in_trace_do_not_confuse_coverage_count(monkeypatch):
    """Trace with non-commitment tools mixed in (e.g. web_search) must count only
    the commitment-relevant ones toward the >=2 threshold."""
    _stub_trace(monkeypatch, ["ask_email_agent", "web_search", "ask_messaging_agent", "read_file"])
    assert _absence_unchecked_sources("Nothing found.", "Do I have anything today?", "rid") == set()


def test_none_intent_is_safe_default(monkeypatch):
    """intent=None (e.g. classify_message() failed upstream) must behave like
    intent='' — no crash, no false trigger from intent alone."""
    _stub_trace(monkeypatch, [])
    assert _absence_unchecked_sources("Sent.", "Send an email to Bob", "rid", intent=None) == set()
    assert _needs_claim_check("Send an email to Bob", "rid", intent=None) == []


def test_needs_claim_check_preserves_trace_order(monkeypatch):
    from store import trace_store
    ordered = [{"tool": "ask_calendar_agent"}, {"tool": "ask_email_agent"}, {"tool": "ask_tasks_agent"}]
    monkeypatch.setattr(trace_store, "get_trace", lambda rid: ordered)
    result = _needs_claim_check("Do I have anything today?", "rid")
    assert result == ordered


def test_orchestrator_retries_exactly_once(monkeypatch):
    """Absence answer + thin coverage → second local_chat call with nudge; result final."""
    calls = []

    def fake_local_chat(**kwargs):
        calls.append(kwargs)
        return "Nothing scheduled today." if len(calls) == 1 else "Found it: booking at 8:25 AM."

    _stub_trace(monkeypatch, ["ask_calendar_agent"])
    monkeypatch.setattr(harness, "local_chat", fake_local_chat)
    monkeypatch.setattr(harness, "_guard_check", lambda *a, **k: None)
    monkeypatch.setattr(harness, "trim_to_budget", lambda h, m, q, chat_id=None: h)

    result, model, path = harness.run(query="Do I have anything scheduled today?", chat_id=None)

    assert len(calls) == 2
    assert "VERIFICATION REQUIRED" in calls[1]["system_prompt"]
    assert "ask_email_agent" in calls[1]["system_prompt"]
    assert result == "Found it: booking at 8:25 AM."
    assert path == "orchestrator"
