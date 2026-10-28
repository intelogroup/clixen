import orb_agent


def _tool(name):
    return {"type": "function", "function": {"name": name}}


def test_only_orb_chats_get_the_lean_mode():
    assert orb_agent.is_orb_chat("orb_ui") and orb_agent.is_orb_chat("orb_babysit_1")
    assert not orb_agent.is_orb_chat("web_ui") and not orb_agent.is_orb_chat(None) and not orb_agent.is_orb_chat("ide_1")


def test_lean_tools_keep_the_screen_and_drop_the_rest():
    full = [_tool(n) for n in ("act", "observe", "update_task_plan", "ask_web_search",
                               "ask_automation_agent", "ask_verse_agent", "send_telegram", "ask_deep_research")]
    assert [t["function"]["name"] for t in orb_agent.lean_tools(full)] == ["act", "observe", "update_task_plan", "ask_web_search"]


def test_every_screen_tool_is_in_the_allowlist():
    # the names harness.py offers the orchestrator for the user's screen
    for n in ("observe", "act", "open_url", "read_page_text"):
        assert n in orb_agent.TOOL_NAMES


def test_prompt_is_short_and_context_blocks_come_first():
    p = orb_agent.system_prompt("NOW: today", "", "PLAN: a")
    assert p.startswith("NOW: today\nPLAN: a\n") and p.endswith(orb_agent.PROMPT)
    # Ladder of this guard: 4_500 -> 4_700 (the one-line note that tool
    # descriptions are shortened) -> 3_000 after the prose was rewritten without
    # losing a rule (2_839 chars, measured 2026-10-25: same tool choice and same
    # answers on four task shapes, 4_767 -> 4_347 round-0 prompt tokens with
    # fresh chat ids) -> 3_200 for the workspace line, which is the only place
    # that tells the model its files live in its own folder and NOT in the
    # user's Documents. Cheap at 231 chars: the alternative is a model that
    # reaches for write_file (not granted) or writes into ~/Documents.
    # The orchestrator prompt is still ~13_800 chars.
    assert len(orb_agent.PROMPT) < 3200


def test_the_orb_cannot_send_mail_behind_the_screen():
    assert "ask_email_agent" not in orb_agent.TOOL_NAMES and "send_email" not in orb_agent.TOOL_NAMES


def test_prompt_teaches_the_gmail_recipe():
    assert "app_task" in orb_agent.PROMPT and "gmail.send" in orb_agent.PROMPT


def test_prompt_names_the_orb_purpose_and_visual_skill():
    assert "voice of the floating orb" in orb_agent.PROMPT
    assert "load_skill(browse-visually)" in orb_agent.PROMPT
    assert "Login wall" in orb_agent.PROMPT


def test_browse_visually_procedure_exists_and_fits():
    import tools.skills as _skills
    assert "browse-visually" in _skills.list_skills()
    text = _skills.load_skill("browse-visually")
    assert text.startswith("## Procedure: browse-visually")
    assert len(text.encode("utf-8")) <= _skills.MAX_SKILL_BYTES
