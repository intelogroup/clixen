import orb_agent


def _tool(name):
    return {"type": "function", "function": {"name": name}}


def test_only_orb_chats_get_the_lean_mode():
    assert orb_agent.is_orb_chat("orb_ui") and orb_agent.is_orb_chat("orb_babysit_1")
    assert not orb_agent.is_orb_chat("web_ui") and not orb_agent.is_orb_chat(None) and not orb_agent.is_orb_chat("ide_1")


def test_lean_tools_keep_the_screen_and_drop_the_rest():
    full = [_tool(n) for n in ("click_text", "type_text", "update_task_plan", "ask_web_search",
                               "ask_automation_agent", "ask_verse_agent", "send_telegram", "ask_deep_research")]
    assert [t["function"]["name"] for t in orb_agent.lean_tools(full)] == ["click_text", "type_text", "update_task_plan", "ask_web_search"]


def test_every_screen_tool_is_in_the_allowlist():
    # the names harness.py offers the orchestrator for the user's screen
    for n in ("get_screen_context", "click_text", "type_text", "press_keys", "scroll_page", "open_url", "read_page_text"):
        assert n in orb_agent.TOOL_NAMES


def test_prompt_is_short_and_context_blocks_come_first():
    p = orb_agent.system_prompt("NOW: today", "", "PLAN: a")
    assert p.startswith("NOW: today\nPLAN: a\n") and p.endswith(orb_agent.PROMPT)
    assert len(orb_agent.PROMPT) < 3000  # the orchestrator prompt is ~13,800 chars


def test_the_orb_cannot_send_mail_behind_the_screen():
    assert "ask_email_agent" not in orb_agent.TOOL_NAMES and "send_email" not in orb_agent.TOOL_NAMES
