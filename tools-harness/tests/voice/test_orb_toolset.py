"""The orb's tool allowlist must stay callable, complete, and honest.

These are invariants about the *set*, not about one tool. Each one exists
because the corresponding failure already happened in production and was only
found by hand:

- a name in the allowlist with no handler behind it -> "Unknown tool" at call
  time, one wasted round, and the model tries again;
- observe() telling the model to use get_screen_context / click_text while
  neither was granted -> the orb is blind on any app without an accessibility
  tree and is handed advice it is forbidden to follow (found 2026-10-24 while
  auditing the surface);
- allowlisting an Ollama-routed subagent (ask_calendar_agent, 113 calls) while
  Ollama is down -> every calendar ask fails after a whole round with
  "Failed to connect to Ollama".
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import orb_agent  # noqa: E402
from tools import registry  # noqa: E402

_SCHEMAS = {t["function"]["name"]: t["function"] for t in registry.ALL_TOOLS}


def test_every_allowlisted_tool_has_a_schema_and_a_handler():
    missing = sorted(
        n for n in orb_agent.TOOL_NAMES
        if n not in _SCHEMAS or n not in registry.EXECUTORS
    )
    assert not missing, f"allowlisted but unusable (no schema/handler): {missing}"


def test_the_allowlist_does_not_carry_unregistered_names():
    """A typo in TOOL_NAMES is silently a missing capability."""
    assert orb_agent.TOOL_NAMES <= set(_SCHEMAS)


def test_every_tool_observe_points_at_is_actually_granted():
    """observe() ends with 'try get_screen_context or click_text OCR' on apps
    with no accessibility tree. Whatever it names must be in the allowlist."""
    src = (Path(__file__).resolve().parents[2] / "tools" / "ax_context.py").read_text()
    hints = re.findall(r"try (get_screen_context) or (click_text)", src)
    assert hints, "observe's no-AX hint text moved; update this test with it"
    named = set(hints[0])
    assert named <= orb_agent.TOOL_NAMES, (
        f"observe tells the model to use {sorted(named - orb_agent.TOOL_NAMES)}, "
        "which the orb is not allowed to call"
    )


def test_no_allowlisted_tool_needs_the_local_ollama_model():
    """Subagents whose intent routes to Ollama are dead whenever it is down."""
    local_only = {"ask_calendar_agent", "ask_tasks_agent", "ask_email_agent",
                  "ask_messaging_agent", "ask_automation_agent"}
    assert not (orb_agent.TOOL_NAMES & local_only)


def test_lean_tools_returns_the_whole_allowlist_from_the_full_registry():
    """Regression: lean_tools used to filter the orchestrator's list, so any
    allowlisted tool that list did not carry was silently unavailable — the orb
    called get_screen_context twice instead of get_latest_email for "read my
    last email". Selecting from the full registry makes the allowlist
    authoritative."""
    picked = {t["function"]["name"] for t in orb_agent.lean_tools(registry.ALL_TOOLS)}
    assert picked == set(orb_agent.TOOL_NAMES)


def test_the_data_tools_are_handed_to_the_model_not_just_allowlisted():
    handed = {t["function"]["name"] for t in orb_agent.lean_tools(registry.ALL_TOOLS)}
    assert {"get_latest_email", "list_emails", "read_email"} <= handed
    assert {"list_calendar_events", "create_calendar_event"} <= handed
    assert {"list_tasks", "complete_task", "list_reminders"} <= handed


def test_the_direct_data_tools_that_replaced_the_subagents_are_present():
    assert {
        "get_latest_email", "list_emails", "read_email",
        "list_calendar_events", "create_calendar_event",
        "list_tasks", "complete_task", "list_reminders",
    } <= orb_agent.TOOL_NAMES


def test_the_orb_can_read_mail_and_answer_without_opening_a_tab():
    """'read my last email' used to cost open_url + read_page_text (2-3 rounds)."""
    assert "get_latest_email" in orb_agent.TOOL_NAMES
    assert "get_latest_email" in orb_agent.PROMPT


def test_the_screen_fallback_chain_is_granted_and_documented():
    assert {"get_screen_context", "click_text", "type_text"} <= orb_agent.TOOL_NAMES
    assert "click_text" in orb_agent.PROMPT
    assert "no accessibility tree" in orb_agent.PROMPT.lower()


def test_the_orb_no_longer_carries_the_ceremonial_tools():
    """Dropped because nothing used them (7_520 logged calls, 2026-10-24) or
    because they are the wrong surface for a screen agent. If one comes back,
    it should be because a measurement changed."""
    dropped = {"recall_about", "space", "close_tabs", "dock", "contacts_resolve"}
    assert not (orb_agent.TOOL_NAMES & dropped)


def test_voice_gets_the_same_tools_as_the_orb_minus_the_clock():
    tools = registry.ALL_TOOLS
    voice = {t["function"]["name"] for t in orb_agent.voice_tools(tools)}
    assert voice == set(orb_agent.TOOL_NAMES) - orb_agent.VOICE_EXCLUDED_TOOLS


def test_the_prompt_never_names_a_tool_the_orb_cannot_call():
    """Same failure class as observe's no-AX hint: the prompt told the model to
    use tools that were not granted, so it called them and got denied."""
    import re

    mentioned = {
        n for n in _SCHEMAS
        if re.search(r"\b" + re.escape(n) + r"\b", orb_agent.PROMPT)
    }
    assert mentioned <= orb_agent.TOOL_NAMES, (
        f"prompt names ungranted tools: {sorted(mentioned - orb_agent.TOOL_NAMES)}"
    )


def test_tabs_defaults_to_listing_instead_of_failing_validation():
    """'tabs rejected: ... is a required property' cost a whole round; the
    handler already defaulted to list, only the schema insisted on action."""
    props = _SCHEMAS["tabs"]["parameters"]
    assert "action" not in props.get("required", [])


def test_the_voice_prompt_resolves_relative_dates_without_a_clock_tool():
    """Voice has no get_current_time, but the calendar tools demand an absolute
    date. The injected date line is the clock; the prompt has to say so."""
    head = orb_agent.VOICE_PROMPT_HEAD
    assert "absolute date" in head
    assert "there is no clock to read" in head
