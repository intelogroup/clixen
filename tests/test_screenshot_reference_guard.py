"""
Regression test for the screenshot-reference bug found 2026-07-01: a follow-up like
"use data from the last screenshot" was misread as a request to take a brand-new
screenshot (because it contains the word "screenshot"), short-circuiting before the
message ever reached the data the user was pointing back at.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

from telegram_bot import _is_screenshot_request, _is_screenshot_continuation


def test_backreference_to_prior_screenshot_is_not_a_new_capture():
    assert _is_screenshot_request("You have to use data from the ast screenshot") is False
    assert _is_screenshot_request("what did the previous screenshot say") is False
    assert _is_screenshot_request("in that screenshot what was the answer") is False


def test_unrelated_verb_elsewhere_in_message_does_not_defeat_the_reference_guard():
    # A verb like "send" referring to sending the *excel file*, not the screenshot, must not
    # override the back-reference guard just because it appears somewhere in the sentence.
    assert _is_screenshot_request(
        "You have to use data from the ast screenshot, "
        "Can you save this results in a excel sheet for me and send it to me"
    ) is False


def test_capture_verb_still_triggers_a_new_screenshot():
    assert _is_screenshot_request("Take a screenshot of my Mac and analyze the question") is True
    assert _is_screenshot_request("send me a screenshot") is True
    assert _is_screenshot_request("Show me my screen") is True


def test_continuation_after_a_real_screenshot_triggers_a_new_capture():
    # Bug found 2026-07-01: "Keep going, same as usual" has no screenshot/screen keyword, so
    # it fell through to plain casual chat, and the model fabricated a fake
    # "[Raw OCR text from this screenshot...]" block mimicking the real one from the prior
    # turn — a convincing-looking answer to a screenshot that was never actually taken.
    history = [
        {"role": "assistant", "content": "against.\n\n[Raw OCR text from this screenshot, for later reference: ...]"},
    ]
    with patch("store.conversation.get", return_value=history):
        assert _is_screenshot_continuation("Keep going, same as usual.", "chat1") is True
        assert _is_screenshot_continuation("What is the capital of France?", "chat1") is False


def test_continuation_without_a_prior_screenshot_does_not_trigger():
    history = [{"role": "assistant", "content": "Just a normal chat reply, no screenshot."}]
    with patch("store.conversation.get", return_value=history):
        assert _is_screenshot_continuation("Keep going, same as usual.", "chat1") is False
