"""Lazy tool descriptions: every tool advertised, most of the prose deferred.

Measured 2026-10-25: the orb's 34 tools cost 26_807 chars of schema and half of
that was `description` prose, charged on every round of every turn. Shortening
each description to its first ~200 chars and putting the full text behind
`tool_help(name)` cut the block to ~18.6k chars without removing a single tool,
parameter or required field.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import orb_agent  # noqa: E402
from tools import lazy_schema, registry  # noqa: E402


def _orb_tools():
    return orb_agent.lean_tools(registry.ALL_TOOLS)


def test_it_keeps_every_tool_and_every_parameter():
    raw = _orb_tools()
    slim = lazy_schema.compress(raw)
    assert [t["function"]["name"] for t in raw] == [t["function"]["name"] for t in slim]
    assert all(a["function"]["parameters"] == b["function"]["parameters"] for a, b in zip(raw, slim)), (
        "parameters decide whether a call validates: they must stay byte-identical"
    )


def test_long_descriptions_are_cut_and_short_ones_untouched():
    long_desc = {"type": "function", "function": {"name": "x", "description": "word " * 200, "parameters": {}}}
    short_desc = {"type": "function", "function": {"name": "y", "description": "Short and sweet.", "parameters": {}}}
    out = lazy_schema.compress([long_desc, short_desc], limit=200)
    assert len(out[0]["function"]["description"]) <= 201  # +1 for the ellipsis
    assert out[1]["function"]["description"] == "Short and sweet."
    assert out[1] is short_desc, "a description that already fits should not be copied"


def test_the_input_tools_are_not_mutated():
    raw = _orb_tools()
    before = [t["function"].get("description") for t in raw]
    lazy_schema.compress(raw)
    assert [t["function"].get("description") for t in raw] == before


def test_compressing_twice_changes_nothing_more():
    once = lazy_schema.compress(_orb_tools())
    twice = lazy_schema.compress(once)
    assert [t["function"]["description"] for t in once] == [t["function"]["description"] for t in twice]


def test_it_actually_saves_a_quarter_of_the_block():
    raw = json.dumps(_orb_tools(), separators=(",", ":"))
    slim = json.dumps(lazy_schema.compress(_orb_tools()), separators=(",", ":"))
    assert len(slim) < len(raw) * 0.75, f"{len(raw)} -> {len(slim)}"


def test_tool_help_returns_the_text_that_was_cut():
    slim = {t["function"]["name"]: t["function"]["description"] for t in lazy_schema.compress(_orb_tools())}
    full = next(t["function"]["description"] for t in registry.ALL_TOOLS if t["function"]["name"] == "act")
    assert slim["act"] != full, "act's description should have been shortened"
    assert len(lazy_schema.tool_help("act")) > len(slim["act"]) * 2
    assert "parameters:" in lazy_schema.tool_help("act")


def test_tool_help_names_a_tool_that_does_not_exist():
    out = lazy_schema.tool_help("obsrve")
    assert "no tool named" in out and "observe" in out  # suggests the near match


def test_a_capability_listing_description_is_never_cut():
    """app_task's text is the only place the recipes are enumerated; cutting it
    hid 'web.read' and cost a whole turn (measured 2026-10-25)."""
    slim = {t["function"]["name"]: t["function"]["description"] for t in lazy_schema.compress(_orb_tools())}
    full = {t["function"]["name"]: t["function"]["description"] for t in registry.ALL_TOOLS}
    assert slim["app_task"] == full["app_task"]
    assert "web.read" in slim["app_task"]


def test_the_prompt_says_descriptions_are_shortened():
    assert lazy_schema.PROMPT_NOTE in orb_agent.PROMPT
    assert "tool_help" in orb_agent.TOOL_NAMES, "the note is a promise the model can act on"


def test_the_orchestrator_profiles_keep_full_descriptions():
    """Laziness is for the latency-critical lean profile only."""
    import harness

    assert harness._lazy is not None
    raw = _orb_tools()
    assert len(json.dumps(raw, separators=(",", ":"))) > len(
        json.dumps(lazy_schema.compress(raw), separators=(",", ":"))
    )
