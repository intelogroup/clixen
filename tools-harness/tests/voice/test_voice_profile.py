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


# ── screen context (B1): the orb starts a turn knowing where it is ──────────


def test_screen_block_is_one_compact_line():
    block = orb_agent.screen_block({
        "app": "Google Chrome",
        "window": "USPS Tracking | USPS",
        "url": "https://tools.usps.com/go/TrackConfirmAction",
        "focus_role": "AXTextField",
        "focus_label": "Search",
        "focus_value": "",
        "popovers": [],
    })
    assert block.startswith("SCREEN (snapshot taken for this turn")
    assert "app=Google Chrome" in block
    assert "url=https://tools.usps.com" in block
    assert 'focus=AXTextField "Search"' in block
    assert "\n" not in block, "one line: it is prepended to every spoken turn"


def test_screen_block_is_empty_when_the_snapshot_failed():
    assert orb_agent.screen_block({}) == ""
    assert orb_agent.screen_block({"app": ""}) == ""


def test_screen_block_trims_long_values():
    block = orb_agent.screen_block({"app": "A", "window": "w" * 300, "url": "u" * 400})
    assert len(block) < 300


def test_the_orb_prompt_carries_the_screen_block_when_present():
    prompt = orb_agent.system_prompt_with_screen({"app": "Ghostty"}, "TIME: now")
    assert "app=Ghostty" in prompt
    assert "TIME: now" in prompt
    assert orb_agent.PROMPT in prompt


def test_the_orb_prompt_omits_the_block_when_unavailable():
    prompt = orb_agent.system_prompt_with_screen({}, "TIME: now")
    assert "SCREEN (" not in prompt
    assert orb_agent.PROMPT in prompt


def test_screen_context_can_be_switched_off(monkeypatch):
    import harness

    monkeypatch.setenv("CLIXEN_SCREEN_CONTEXT", "0")
    assert harness._screen_ctx() == {}


def test_screen_context_is_fail_safe(monkeypatch):
    import harness

    monkeypatch.delenv("CLIXEN_SCREEN_CONTEXT", raising=False)
    monkeypatch.setattr(harness, "_screen_ctx_cache", {"at": 0.0, "ctx": {}})

    import tools.ax_context as axc

    def boom():
        raise RuntimeError("AX is angry")

    monkeypatch.setattr(axc, "front_context", boom)
    assert harness._screen_ctx() == {}, "an AX failure must degrade to no block"
