"""
Regression tests for the 2026-07-11 "retire regex-primary classification"
change: harness.run() now calls clients.router.classify_message() (LLM
primary, regex fallback only on failure) instead of the bare regex
classify()/classify_telegram() directly, and harness.py's _forced_tool
block trusts the already-classified `intent` instead of re-scanning raw
query text with its own independent regex (_AUTOMATION_RE/_is_temporal).

Root cause this fixes: an automation named "Daily Tech News Brief" tripped
_TEMPORAL_RE's bare "news" match via the old raw-regex forced-tool check,
forcing a wasted ask_web_search call on a plain pause/resume/delete
command — because raw regex has no notion that "news" was inside a proper
noun, not a topic. LLM classification reads the whole sentence and
shouldn't misfire the same way.
"""
from __future__ import annotations

from unittest.mock import patch

import harness
from clients.router import Classification


def _cls(intent: str, model: str = "openrouter/deepseek/deepseek-v4-flash", hint=None) -> Classification:
    return Classification(model=model, intent=intent, specialist_hint=hint, source="test")


def test_run_calls_classify_message_not_bare_regex_when_model_none():
    with (
        patch("harness.classify_message", return_value=_cls("casual")) as mock_cls,
        patch("harness.local_chat", return_value="ok"),
    ):
        harness.run("hello there", orchestrated=False)

    mock_cls.assert_called_once()


def test_run_threads_channel_into_classify_message():
    with (
        patch("harness.classify_message", return_value=_cls("casual")) as mock_cls,
        patch("harness.local_chat", return_value="ok"),
    ):
        harness.run("hello there", orchestrated=False, channel="telegram")

    args, kwargs = mock_cls.call_args
    channel = kwargs.get("channel") or (args[1] if len(args) > 1 else None)
    assert channel == "telegram"


def test_forced_tool_trusts_automation_intent_not_raw_regex():
    """The exact blindspot: a query whose text contains a temporal word
    ("news") inside a proper noun, but whose real intent (as classified) is
    automation management, not a search. The old raw-regex forced_tool logic
    would have forced ask_web_search here; intent-driven logic must not.
    """
    with (
        patch("harness.classify_message", return_value=_cls("automation")),
        patch("harness.local_chat", return_value="done") as mock_local_chat,
    ):
        harness.run("pause the Daily Tech News Brief automation", orchestrated=True, chat_id=None)

    forced = mock_local_chat.call_args.kwargs.get("force_tool_choice")
    assert forced == "ask_automation_agent"


def test_forced_tool_still_fires_for_real_temporal_intent():
    with (
        patch("harness.classify_message", return_value=_cls("temporal")),
        patch("harness.local_chat", return_value="done") as mock_local_chat,
    ):
        harness.run("what's the weather in Tokyo right now", orchestrated=True, chat_id=None)

    forced = mock_local_chat.call_args.kwargs.get("force_tool_choice")
    assert forced == "ask_web_search"


def test_forced_tool_none_for_unrelated_intent():
    with (
        patch("harness.classify_message", return_value=_cls("casual")),
        patch("harness.local_chat", return_value="done") as mock_local_chat,
    ):
        harness.run("tell me a joke", orchestrated=True, chat_id=None)

    forced = mock_local_chat.call_args.kwargs.get("force_tool_choice")
    assert forced is None
