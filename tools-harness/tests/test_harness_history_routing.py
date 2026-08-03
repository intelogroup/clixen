"""
Regression guard for the context-blind websearch bug: a meta follow-up must be answered
from history via the chat model, NOT sent to the web search pipeline.

Fully hermetic — every external touchpoint (model, search, store, memory) is stubbed, so
this runs offline and deterministically.

Exercises harness.run(orchestrated=False) — the classic per-intent path — explicitly.
Since the 2026-07 cloud-first revamp, orchestrated=True is run()'s default and routes
through a different mechanism entirely (ORCHESTRATOR_SYSTEM_PROMPT + ask_* tool-calling,
the cloud model decides what to do) that doesn't call classify()/local_chat() the way this
test mocks them. orchestrated=False is still real, live code — it's what
_execute_intent_pipeline() uses for pre-classified subagent dispatch — so this remains a
valid regression guard for that path, just not for run()'s default entry point.
"""

import harness
from clients.router import Classification


class _Recorder:
    def __init__(self, ret):
        self.ret = ret
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.ret


def _patch_common(monkeypatch, history):
    # 2026-07-11: run()'s model=None path now calls classify_message() (LLM-primary,
    # regex-fallback) instead of the bare regex classify() — patch that instead so this
    # stays hermetic (no real cloud call) and preserves the original test setup's intent.
    monkeypatch.setattr(
        harness, "classify_message",
        lambda q, channel="web": Classification(model="gemma4:12b-mlx", intent="factual_qa", specialist_hint=None, source="test"),
    )
    monkeypatch.setattr(harness, "_guard_check", lambda q, has_history=False: None)
    monkeypatch.setattr(harness, "conv_get", lambda cid: list(history))
    monkeypatch.setattr(harness, "conv_append", lambda *a, **k: None)
    monkeypatch.setattr(harness, "compact_old_turns", lambda *a, **k: None)
    monkeypatch.setattr(harness, "trim_to_budget", lambda raw, model, q, chat_id=None: list(raw))
    monkeypatch.setattr(harness, "memory_recall", lambda *a, **k: "")


_HIST = [
    {"role": "user", "content": "France vs Sweden final score"},
    {"role": "assistant", "content": "France won 3-0."},
]


def test_meta_followup_answers_from_history_not_web(monkeypatch):
    _patch_common(monkeypatch, _HIST)
    chat = _Recorder("Sorry — my earlier score was wrong.")
    search = _Recorder("SEARCH RESULT")
    monkeypatch.setattr(harness, "local_chat", chat)
    monkeypatch.setattr(harness, "_run_websearch", search)

    result, model, intent = harness.run(
        "Why did you give me false info in the first place?",
        chat_id="test_meta_chat",
        orchestrated=False,
    )

    assert search.calls == [], "meta follow-up must NOT hit the web search pipeline"
    assert chat.calls, "meta follow-up must be answered by the chat model"
    # history was threaded into the chat call
    assert chat.calls[0][1]["history"] == _HIST
    assert model == "chat-followup"
    assert result == "Sorry — my earlier score was wrong."


def test_self_contained_query_still_searches(monkeypatch):
    _patch_common(monkeypatch, _HIST)
    chat = _Recorder("CHAT")
    search = _Recorder("SEARCH RESULT")
    monkeypatch.setattr(harness, "local_chat", chat)
    monkeypatch.setattr(harness, "_run_websearch", search)
    # livescore lookup must not intercept this non-sports query
    monkeypatch.setattr("tools.livescore.is_live_score_query", lambda q: False)

    result, model, intent = harness.run(
        "What is the capital of Australia?",
        chat_id="test_standalone",
        orchestrated=False,
    )

    assert search.calls, "self-contained factual query should reach web search"
    assert model == "websearch"
