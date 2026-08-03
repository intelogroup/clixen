"""Deterministic unit tests for conversational follow-up resolution (no network)."""

from tools.followup import (
    classify_followup,
    _is_meta_followup,
    _needs_context,
)

_HIST = [
    {"role": "user", "content": "France vs Sweden final score"},
    {"role": "assistant", "content": "France won 3-0."},
]


def test_meta_followups_route_to_chat():
    for q in [
        "Why did you give me false info in the first place?",
        "you're wrong",
        "you are wrong about that",
        "are you sure?",
        "that's not correct",
        "what did you say earlier?",
        "I didn't ask that",
        "your last answer was off",
    ]:
        kind, resolved = classify_followup(q, _HIST)
        assert kind == "chat", f"{q!r} → {kind}"
        assert resolved == q


def test_meta_needs_history():
    # Same wording with no history is not a meta follow-up — nothing to refer back to.
    assert classify_followup("you're wrong", [])[0] == "standalone"


def test_self_contained_is_standalone():
    for q in [
        "What is the capital of Australia?",
        "Real Madrid latest transfer news",
        "weather in Tokyo tomorrow",
    ]:
        kind, resolved = classify_followup(q, _HIST)
        assert kind == "standalone", f"{q!r} → {kind}"
        assert resolved == q


def test_context_dependent_detected():
    # Deterministic layer only — the LLM rewrite is exercised in a live test.
    for q in ["and the second half?", "what about their goalkeeper?", "how about the other team?"]:
        assert _needs_context(q, _HIST), q
        assert not _is_meta_followup(q), q


def test_why_question_is_not_meta():
    # A genuine "why" web question must not be swallowed by the meta pattern.
    assert not _is_meta_followup("why is the sky blue")
    assert not _is_meta_followup("why did the roman empire fall")


def test_no_history_never_needs_context():
    assert not _needs_context("and the second half?", [])
