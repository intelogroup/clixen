"""
Parity check for clients.router.classify_message() — the unified entry point
that replaced telegram_bot.py/whatsapp_bot.py/harness.py each independently
re-classifying the same message text (see the dry-run trace that motivated
this: classify_telegram and harness's own classify() could disagree on the
same query). The LLM stage is mocked so this stays offline/deterministic;
these are exactly the disagreement cases from that trace.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clients.router import classify_message


# (query, channel, expected_intent) — one representative query per Stage-0/fallback path,
# taken from the original dry-run trace's disagreement cases.
_CASES = [
    ("take a screenshot", "web", "ocr"),
    ("what's the last email", "telegram", "email"),
    ("send this as a .md", "telegram", "document"),
    ("what was the last question I asked you", "telegram", "casual"),
    ("what is the capital of Japan", "telegram", "factual_qa"),
    ("list files in Downloads", "telegram", "filesystem"),
]


def test_fallback_parity():
    """With the LLM stage forced to fail, classify_message() must match the
    regex fallback's known-good intents (guards fire even without the LLM)."""
    with patch("clients.router._llm_classify", return_value=None):
        for query, channel, expected in _CASES:
            cls = classify_message(query, channel=channel)
            assert cls.intent == expected, f"{query!r} (channel={channel}): got {cls.intent!r}, expected {expected!r}"


def test_document_intent_gated_by_channel():
    """document intent only applies on telegram/whatsapp — web/ide never emit it."""
    with patch("clients.router._llm_classify", return_value=None):
        cls = classify_message("send this as a .md", channel="web")
        assert cls.intent != "document"


def test_llm_stage_result_used_when_available():
    """When the LLM stage succeeds, its Classification is returned as-is."""
    from clients.router import Classification

    fake = Classification(model="gemma4:12b-mlx", intent="browser", specialist_hint=None, source="llm")
    with patch("clients.router._llm_classify", return_value=fake):
        cls = classify_message("log into my bank account", channel="web")
        assert cls is fake


def test_warm_classifier_cloud():
    """warm_classifier() now hits cloud API — must not raise on best-effort."""
    from clients.router import warm_classifier
    warm_classifier()  # must not raise; network error is fine


def _demo() -> None:
    """Offline self-check — no network required (LLM stage mocked)."""
    test_fallback_parity()
    test_document_intent_gated_by_channel()
    test_llm_stage_result_used_when_available()
    test_warm_classifier_cloud()
    print("all classify_message checks passed")


if __name__ == "__main__":
    _demo()
