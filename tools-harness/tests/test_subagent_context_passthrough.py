"""Regression: a pronoun-only follow-up forwarded to a STATELESS subagent was silently
swapped for a generic clarifying question the user never saw.

Live repro 2026-09-24 (chat 8538224711): "Send a message to Solini." → the orchestrator
forwarded "Search for it live" to ask_web_search → harness's ambiguity guard fired with
has_history=False (subagents ran with chat_id=None) → ask_web_search returned "What are you
referring to? ..." in 204ms and the turn quietly degraded (trace: `tools=ask_web_search
errors=1`, elapsed 204ms).

Two halves, both covered here:
  1. tools.query_guard.CONTEXT_PRESENT — the guard still fires cold, but knows a conversation
     exists when a subagent was spawned from one.
  2. tools.orchestrator_tools._dereferenced_query — the referent is actually supplied, and
     already-self-contained queries stay byte-identical.
"""
import pytest

import harness
from store import conversation
from tools import orchestrator_tools
from tools.query_guard import CONTEXT_PRESENT, check_all


@pytest.fixture(autouse=True)
def _no_flag_leak():
    assert CONTEXT_PRESENT.get() is False  # a leaked True would make later tests lie
    yield
    CONTEXT_PRESENT.set(False)


# ── guard flag ────────────────────────────────────────────────────────────────

def test_guard_still_fires_cold():
    assert check_all("Search for it live") is not None


def test_guard_passes_when_context_present():
    CONTEXT_PRESENT.set(True)
    assert check_all("Search for it live") is None


def test_explicit_has_history_wins_over_flag():
    # harness always passes has_history explicitly for the orchestrator path — the
    # subagent flag must never override it in either direction.
    CONTEXT_PRESENT.set(True)
    assert check_all("Search for it live", has_history=False) is not None

    CONTEXT_PRESENT.set(False)
    assert check_all("Search for it live", has_history=True) is None


def test_intent_pipeline_sets_flag_in_subagent_thread(monkeypatch):
    """The subagent runs in a pool WORKER thread, which doesn't inherit ContextVars — so
    the flag has to be set inside that thread (setting it in the caller is invisible).
    fake_run stands in for that thread's body."""
    seen = {}

    def fake_run(*, query, intent, chat_id, orchestrated, run_id):
        seen["flag_in_thread"] = CONTEXT_PRESENT.get()
        return ("ok", "model", "intent")

    monkeypatch.setattr(harness, "run", fake_run)

    assert harness._execute_intent_pipeline("temporal", "q", chat_id="8538224711") == "ok"
    assert seen["flag_in_thread"] is True
    assert CONTEXT_PRESENT.get() is False  # reset on exit, no leak into the caller

    harness._execute_intent_pipeline("temporal", "q", chat_id=None)
    assert seen["flag_in_thread"] is False


# ── de-reference ──────────────────────────────────────────────────────────────

@pytest.fixture
def _chat_with_solini_turn(monkeypatch):
    monkeypatch.setattr(orchestrator_tools, "_get_chat_id", lambda: "8538224711")
    monkeypatch.setattr(
        conversation,
        "get",
        lambda cid: [
            {"role": "user", "content": "Send a message to Solini."},
            {"role": "assistant", "content": "Who should I contact?"},
        ],
    )


def test_deref_prefixes_pronoun_only_query(_chat_with_solini_turn):
    out = orchestrator_tools._dereferenced_query("Search for it live")

    assert out != "Search for it live"
    assert "Send a message to Solini." in out
    assert out.endswith("Search for it live")
    # and the repaired query must survive the guard that rejected the original
    assert check_all(out) is None


def test_deref_leaves_self_contained_query_alone(monkeypatch):
    q = "summarize the clixen roadmap"
    assert check_all(q) is None  # premise: nothing for the guard to complain about

    monkeypatch.setattr(orchestrator_tools, "_get_chat_id", lambda: "8538224711")
    monkeypatch.setattr(conversation, "get", lambda cid: [{"role": "user", "content": "hi"}])

    assert orchestrator_tools._dereferenced_query(q) == q


def test_deref_without_chat_id_is_a_passthrough(monkeypatch):
    monkeypatch.setattr(orchestrator_tools, "_get_chat_id", lambda: None)
    assert orchestrator_tools._dereferenced_query("Search for it live") == "Search for it live"


def test_deref_with_empty_history_is_a_passthrough(monkeypatch):
    monkeypatch.setattr(orchestrator_tools, "_get_chat_id", lambda: "8538224711")
    monkeypatch.setattr(conversation, "get", lambda cid: [])
    assert orchestrator_tools._dereferenced_query("Search for it live") == "Search for it live"


def test_deref_never_raises(monkeypatch):
    """Context repair is best-effort — a broken store must not break dispatch."""
    monkeypatch.setattr(orchestrator_tools, "_get_chat_id", lambda: "8538224711")

    def boom(cid):
        raise RuntimeError("conversation store unavailable")

    monkeypatch.setattr(conversation, "get", boom)
    assert orchestrator_tools._dereferenced_query("Search for it live") == "Search for it live"
