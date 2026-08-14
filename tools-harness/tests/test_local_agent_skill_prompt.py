import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.local_agent_nodes import _get_system_prompt, _tools_for_state
from agents.local_agent_state import LocalAgentState


def test_skill_prompt_injected_into_system_prompt():
    prompt = _get_system_prompt(
        "gemma4:12b", task="document", tools=[],
        skill_prompt="[SKILL: Test Skill — does a thing]\n\nSTEP 1: do the thing.",
    )
    assert "[SKILL: Test Skill — does a thing]" in prompt
    assert "STEP 1: do the thing." in prompt


def test_no_skill_prompt_leaves_output_unchanged():
    without = _get_system_prompt("gemma4:12b", task="document", tools=[])
    with_none = _get_system_prompt("gemma4:12b", task="document", tools=[], skill_prompt=None)
    assert without == with_none
    assert "[SKILL:" not in without


def test_document_prompt_exposes_scoped_scratchpad_identity():
    prompt = _get_system_prompt(
        "gemma4:12b", task="document", tools=[], project_root="/tmp/work", chat_id="chat-7"
    )

    assert "document_scratchpad" in prompt
    assert "chat-7" in prompt
    assert "/tmp/work" in prompt


def test_document_prompt_requires_derived_numeric_answers_to_use_notes():
    prompt = _get_system_prompt("gemma4:12b", task="document", tools=[])

    assert "calculate from the supplied evidence" in prompt
    assert "footnotes, captions, and narrative guidance" in prompt
    assert "calculator/run_python" in prompt


def test_local_agent_state_skill_prompt_defaults_to_none():
    state = LocalAgentState(messages=[])
    assert state.skill_prompt is None
    assert state.skill_tools is None


def test_skill_tools_narrows_tool_list():
    state = LocalAgentState(messages=[], task="full", skill_tools=["find_files", "read_file", "write_file"])
    names = {t["function"]["name"] for t in _tools_for_state(state)}
    assert names == {"find_files", "read_file", "write_file"}


def test_no_skill_tools_keeps_full_task_set():
    state = LocalAgentState(messages=[], task="document", skill_tools=None)
    without_skill = {t["function"]["name"] for t in _tools_for_state(state)}
    assert len(without_skill) > 3
    assert "find_files" in without_skill


def test_specialist_exclusions_remove_redundant_tools_but_keep_reads():
    state = LocalAgentState(
        messages=[], task="full", excluded_tools=["find_files", "fd_find"]
    )
    names = {t["function"]["name"] for t in _tools_for_state(state)}
    assert "find_files" not in names
    assert "fd_find" not in names
    assert "read_file" in names
