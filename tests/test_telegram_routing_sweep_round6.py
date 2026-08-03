"""
Regression and edge-case tests (Round 6) covering telegram_bot.py interaction helpers,
screenshot detection, continuation logic, and channel-specific document routing in
classify_message.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

from unittest.mock import patch
import telegram_bot as tb
from clients.router import classify_message, CLOUD_MODEL


def test_telegram_bot_is_screenshot_request():
    assert tb._is_screenshot_request("take a screenshot") is True
    assert tb._is_screenshot_request("take a screen shot") is True
    assert tb._is_screenshot_request("show me my screen") is True
    # Reference only should NOT capture a new one
    assert tb._is_screenshot_request("use data from the last screenshot") is False
    # Capture verb near screenshot overrides reference guard
    assert tb._is_screenshot_request("take a new screenshot and use data from it") is True


def test_telegram_bot_is_screenshot_continuation(monkeypatch):
    # Test with empty history
    monkeypatch.setattr("store.conversation.get", lambda cid: [])
    assert tb._is_screenshot_continuation("keep going", "chat123") is False

    # Test with history where last assistant turn has raw OCR marker
    matching_history = [
        {"role": "user", "content": "take a screenshot"},
        {"role": "assistant", "content": f"{tb._SCREENSHOT_OCR_MARKER}]: ..."}
    ]
    monkeypatch.setattr("store.conversation.get", lambda cid: matching_history)
    assert tb._is_screenshot_continuation("keep going", "chat123") is True
    assert tb._is_screenshot_continuation("again", "chat123") is True
    assert tb._is_screenshot_continuation("continue", "chat123") is True

    # Test with history where last assistant turn does NOT have raw OCR marker
    non_matching_history = [
        {"role": "user", "content": "take a screenshot"},
        {"role": "assistant", "content": "I took a screenshot for you"}
    ]
    monkeypatch.setattr("store.conversation.get", lambda cid: non_matching_history)
    assert tb._is_screenshot_continuation("keep going", "chat123") is False


def test_telegram_bot_send_photo_request():
    # Only photo requests containing share/send/show should trigger photo delivery
    assert bool(tb._SEND_PHOTO_RE.search("send it to me")) is True
    assert bool(tb._SEND_PHOTO_RE.search("show me my screen")) is True
    assert bool(tb._SEND_PHOTO_RE.search("what's on my screen")) is False


def test_classify_message_document_guard_for_telegram():
    # Telegram triggers Stage 0 document guard immediately
    cls_tg = classify_message("make a pdf of my notes", channel="telegram")
    assert cls_tg.intent == "document"
    assert cls_tg.model == CLOUD_MODEL
    assert cls_tg.source == "guard"

    # Web channel bypasses Stage 0 document guard, going to Stage 1 LLM call (which we mock here)
    with patch("clients.router._llm_classify", return_value=None), \
         patch("clients.router._classify_regex_fallback") as mock_fallback:
        classify_message("make a pdf of my notes", channel="web")
        assert mock_fallback.called
