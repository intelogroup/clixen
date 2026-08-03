"""Self-check for brabble_hook.py's spoken yes/no confirmation parsing.

Not a full harness — just the regex/approval logic, since a wrong match here
means a spoken "no" gets read as approval for a git push/reset --hard/etc.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brabble_hook import _looks_like_confirmation_reply, _YES_RE, _NO_RE


def _approved(text: str) -> bool:
    return bool(_YES_RE.search(text)) and not _NO_RE.search(text)


def test_yes_variants_approve():
    for text in ("yes", "Yeah go ahead", "yep do it", "sure, confirm it"):
        assert _looks_like_confirmation_reply(text), text
        assert _approved(text), text


def test_no_variants_deny():
    for text in ("no", "nope", "cancel that", "don't do it", "nah stop"):
        assert _looks_like_confirmation_reply(text), text
        assert not _approved(text), text


def test_unrelated_text_is_not_a_confirmation_reply():
    for text in ("what time is it", "search for pizza places", "read my latest email"):
        assert not _looks_like_confirmation_reply(text), text


def test_conflicting_yes_and_no_in_same_utterance_denies():
    # "no wait yes" style hedging must not accidentally approve a destructive command.
    assert not _approved("no wait yes actually go ahead")


if __name__ == "__main__":
    test_yes_variants_approve()
    test_no_variants_deny()
    test_unrelated_text_is_not_a_confirmation_reply()
    test_conflicting_yes_and_no_in_same_utterance_denies()
    print("OK")
