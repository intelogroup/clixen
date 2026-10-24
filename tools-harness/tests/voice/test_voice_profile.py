"""Voice turns must run the lean orb profile, not the chat coordinator's.

Why this test exists: voice.agent writes history under chat_id "voice_agent", which
does not start with "orb", so the lean-profile gate in harness.run() missed every
spoken turn. Measured 2026-10-24: 16_466-16_992 prompt tokens per voice round versus
5_533 on the orb path -- ~0.3-0.5 s of extra first-token latency on every round,
plus tool selection out of the full 84-tool list.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import orb_agent  # noqa: E402
from voice.agent import VOICE_CHAT_ID  # noqa: E402


def _tools(*names):
    return [{"type": "function", "function": {"name": n, "description": ""}} for n in names]


def test_the_voice_chat_id_is_recognised():
    assert orb_agent.is_voice_chat(VOICE_CHAT_ID)
    assert orb_agent.is_voice_chat("brabble_voice")
    assert not orb_agent.is_voice_chat("web_ui")
    assert not orb_agent.is_voice_chat(None)


def test_the_voice_chat_id_is_not_an_orb_chat():
    """The exact mismatch that caused the regression."""
    assert not orb_agent.is_orb_chat(VOICE_CHAT_ID)


def test_voice_tools_keep_the_screen_tools_and_drop_the_rest():
    tools = _tools("observe", "act", "read_page_text", "send_telegram", "send_email", "delete_workflow")
    kept = {t["function"]["name"] for t in orb_agent.voice_tools(tools)}
    assert {"observe", "act", "read_page_text"} <= kept
    assert not kept & {"send_telegram", "send_email", "delete_workflow"}


def test_voice_does_not_get_the_clock_tool():
    """The time is injected as context; the tool only buys an extra round."""
    tools = _tools("observe", "get_current_time")
    kept = {t["function"]["name"] for t in orb_agent.voice_tools(tools)}
    assert kept == {"observe"}


def test_voice_extras_are_configurable(monkeypatch):
    tools = _tools("observe", "set_reminder")
    assert "set_reminder" not in {t["function"]["name"] for t in orb_agent.voice_tools(tools)}
    monkeypatch.setenv("CLIXEN_VOICE_EXTRA_TOOLS", "set_reminder, get_current_time")
    kept = {t["function"]["name"] for t in orb_agent.voice_tools(tools)}
    assert "set_reminder" in kept


def test_voice_prompt_carries_the_screen_prompt_and_the_spoken_rules():
    prompt = orb_agent.voice_prompt("TIME: now")
    assert "TIME: now" in prompt
    assert orb_agent.PROMPT in prompt
    assert "SPEAKING ALOUD" in prompt


def test_voice_prompt_puts_the_answer_first_rule_ahead_of_the_screen_protocol():
    """Without this ordering the orb's "observe before you act" made trivial
    questions spend a whole extra tool round (measured 2026-10-24)."""
    prompt = orb_agent.voice_prompt("TIME: now")
    assert prompt.index("ANSWER DIRECTLY WHEN YOU CAN") < prompt.index(orb_agent.PROMPT)
    assert "call no tool" in prompt


def test_orb_prompt_is_unchanged():
    """The orb keeps its own prompt: the spoken rules are voice-only."""
    assert "SPEAKING ALOUD" not in orb_agent.system_prompt()
