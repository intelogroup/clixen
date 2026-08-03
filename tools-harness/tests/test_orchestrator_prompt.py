"""Drift guards for load-bearing orchestrator prompt rules.

2026-07-17: ORCHESTRATOR_SYSTEM_PROMPT was split into a lean always-on core
plus topic-scoped fragments (skills_data/orchestrator_fragments/*.py) injected
conditionally per turn by _select_orchestrator_fragments(). Rules that used to
live in the monolithic prompt now live in whichever fragment owns that topic —
these tests check each rule where it now actually lives, not against the raw
core constant.
"""
from harness import ORCHESTRATOR_SYSTEM_PROMPT, _select_orchestrator_fragments
from skills_data.orchestrator_fragments import schedule_fanout


def test_commitment_fanout_rule_present():
    p = schedule_fanout.FRAGMENT
    assert "ask_email_agent" in p and "ask_calendar_agent" in p and "ask_tasks_agent" in p
    assert "FIRST tool round" in p
    assert "only one of these sources" in p


def test_absence_protocol_rule_present():
    p = schedule_fanout.FRAGMENT
    assert "[subagent" in p
    assert "status=ok" in p


def test_core_prompt_has_fragments_placeholder():
    assert "{fragments}" in ORCHESTRATOR_SYSTEM_PROMPT
    assert "{home_dir}" in ORCHESTRATOR_SYSTEM_PROMPT


def test_schedule_query_selects_schedule_fanout_fragment():
    out = _select_orchestrator_fragments("do I have anything scheduled tomorrow?", "test_chat_a")
    assert "ask_calendar_agent" in out


def test_automation_query_selects_automation_dsl_fragment():
    out = _select_orchestrator_fragments("create an automation that watches my inbox", "test_chat_b")
    assert "list_available_pipelines" in out


def test_offtopic_query_selects_no_fragment():
    out = _select_orchestrator_fragments("hello there", "test_chat_c")
    assert out == ""


def test_stickiness_carries_forward_one_turn_only():
    chat_id = "test_chat_d"
    first = _select_orchestrator_fragments("create an automation for my inbox", chat_id)
    assert "list_available_pipelines" in first
    followup = _select_orchestrator_fragments("yes do that", chat_id)
    assert "list_available_pipelines" in followup  # one-turn carryover
    second_followup = _select_orchestrator_fragments("yes do that", chat_id)
    assert second_followup == ""  # stickiness window expired after one turn
