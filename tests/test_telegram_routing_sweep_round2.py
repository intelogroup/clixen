"""
Regression tests from a second 10-query Telegram-simulated sweep covering areas round 1
didn't touch: math routing, git tools on Telegram, copy/append, find_largest's count, and
the ambiguity guard. Each test pins a bug found by actually running the query.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

import harness as m
from clients.router import classify, classify_telegram


def test_classify_routes_bare_arithmetic_to_math_not_factual_qa():
    # classify() was missing the _SIMPLE_MATH_RE check classify_telegram already had, so
    # "17% of 2450" fell through to factual_qa and got resolved via web search.
    _, intent = classify("What is 17% of 2450?")
    assert intent == "math"


def _run_with_captured_tools(**run_kwargs):
    captured = {}

    def fake_local_chat(**kwargs):
        captured.update(kwargs)
        return "ok"

    run_kwargs.setdefault("orchestrated", False)
    with patch("harness.local_chat", side_effect=fake_local_chat), \
         patch("harness._guard_check", return_value=None), \
         patch("harness.parse_local_fs_action", return_value=None):
        m.run(**run_kwargs)
    return {t["function"]["name"] for t in (captured.get("tools") or [])}


def test_git_tools_preserved_for_telegram_style_call():
    # Telegram/WhatsApp always pass an explicit model string (classify_telegram's routing
    # choice, not a user override). This used to trip the "strip IDE-only tools" guard meant
    # only for the web UI's manual model dropdown, silently disabling git/repl on Telegram.
    tool_names = _run_with_captured_tools(
        query="what's the git status of the repo at ~/Developer/clixen",
        model="gemma4:12b-mlx",
        chat_id="123456789",
    )
    assert "git_status" in tool_names


def test_git_tools_stripped_for_manual_web_model_pick():
    # The web UI's "Models" dropdown is a genuine manual override — tools should still be
    # stripped there (this is the behavior the original guard was meant to protect).
    tool_names = _run_with_captured_tools(
        query="what's the git status of the repo at ~/Developer/clixen",
        model="gemma4:12b-mlx",
        chat_id="web_ui",
        manual_model_pick=True,
    )
    assert "git_status" not in tool_names


def test_find_largest_parses_requested_count():
    action = m.parse_local_fs_action("What are the 3 biggest files in ~/Downloads?")
    assert action.tool_name == "find_largest"
    assert action.arguments["n"] == 3


def test_find_largest_defaults_to_five_without_a_count():
    action = m.parse_local_fs_action("What are the biggest files in ~/Downloads?")
    assert action.tool_name == "find_largest"
    assert action.arguments["n"] == 5


def test_classify_routes_self_referential_question_to_casual_not_websearch():
    # _CONVO_REF_RE only covered the user asking about their own past statements ("I told
    # you"), never the assistant's own ("what did you just do") — so "what was the last
    # thing you saw" fell through to factual_qa, which always forces a web search, and the
    # model searched the open web and reported on an unrelated documentary trailer.
    _, intent = classify("What was the last action you did? What was the last thing you see?")
    assert intent == "casual"
    _, intent = classify_telegram("What did you just do?")
    assert intent == "casual"
    # A second phrasing found live during testing — "what was the last X I/you <verb>" is a
    # general conversation-memory form the specific alternatives above didn't cover.
    _, intent = classify_telegram("What was the last question I asked you?")
    assert intent == "casual"
    # Genuine factual questions must still be unaffected.
    _, intent = classify("What is the capital of Japan?")
    assert intent == "factual_qa"
    _, intent = classify("What was the last Formula 1 race?")
    assert intent == "factual_qa"


def test_classify_telegram_routes_md_export_to_document_not_spotlight():
    # _DOC_CREATE_RE was missing md/markdown from its file-type list, so "save it as a .md"
    # fell through to _SPOTLIGHT_RE, which matched "find" (used colloquially, "the info you
    # find") + "screenshot" within its 30-char window and hijacked it into a file-search intent.
    _, intent = classify_telegram(
        "Save the info you find from the last screenshot and send them to me as a .md"
    )
    assert intent == "document"


def test_classify_telegram_search_queries_route_to_factual_qa_not_browser():
    # General knowledge / web-search queries with or without "search the web" phrases
    # should be classified as factual_qa (search graph), never browser.
    _, intent = classify_telegram("Who played Joe Robertson in the tv series “ spider noir”?")
    assert intent == "factual_qa"

    _, intent = classify_telegram("Who played Joe Robertson in the tv series “ spider noir”? Search the web")
    assert intent == "factual_qa"
