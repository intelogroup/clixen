import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.local_agent_nodes import _get_system_prompt
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


def test_local_agent_state_skill_prompt_defaults_to_none():
    state = LocalAgentState(messages=[])
    assert state.skill_prompt is None
