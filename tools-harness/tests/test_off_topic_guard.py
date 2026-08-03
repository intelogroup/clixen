"""
Regression guard for the dead OFF-TOPIC relevance guard: _run_subagent referenced
`result` before assignment (UnboundLocalError swallowed by the except), so the warning
prefix the orchestrator is trained to honor was never produced. The guard now writes to
a dedicated var that survives the footer assembly.
Hermetic — _execute_intent_pipeline and summarize_run are stubbed; no model/network.
"""

import threading

from tools import orchestrator_tools as ot
from log_config import CURRENT_RUN_ID


def _run(intent, result_text, summary_text, monkeypatch):
    token = CURRENT_RUN_ID.set("")
    try:
        import harness as _harness
        monkeypatch.setattr(ot, "_SUBAGENT_CACHE", {})
        # harness and store.trace_store are imported inside _run_subagent's body —
        # import the real modules first (they resolve via sys.modules), then patch
        # attributes on them.
        import store.trace_store as _ts
        monkeypatch.setattr(_harness, "_execute_intent_pipeline", lambda **kw: result_text)
        monkeypatch.setattr(_ts, "summarize_run", lambda rid: {"status": "ok", "tools": ["web_search"], "errors": 0})
        monkeypatch.setattr(ot, "_get_chat_id", lambda: "chat_test_off_topic")
        monkeypatch.setattr(
            "store.conversation._summary_cache",
            {"chat_test_off_topic": {"summary": summary_text}},
        )
        monkeypatch.setattr("store.conversation._summary_lock", threading.Lock())
        return ot._run_subagent(intent, "some query")
    finally:
        CURRENT_RUN_ID.reset(token)


def test_off_topic_guard_appends_warning_on_zero_overlap(monkeypatch):
    result = _run(
        "temporal",
        "quantum waves particles entanglement spin",
        "France beat Belgium yesterday final score extra time",
        monkeypatch,
    )
    assert result.startswith("[OFF-TOPIC WARNING"), (
        "zero-overlap result must carry the OFF-TOPIC warning prefix"
    )


def test_off_topic_guard_stays_quiet_on_related_result(monkeypatch):
    result = _run(
        "temporal",
        "ocean depth covers marine biology research",
        "The ocean covers most of the Earth surface",
        monkeypatch,
    )
    assert not result.startswith("[OFF-TOPIC WARNING"), (
        "overlapping result must not be flagged off-topic"
    )


def test_off_topic_guard_survives_non_relevant_intents(monkeypatch):
    result = _run(
        "weather",
        "rain tomorrow morning",
        "France beat Belgium yesterday final score extra time",
        monkeypatch,
    )
    assert not result.startswith("[OFF-TOPIC WARNING"), (
        "guard only applies to temporal/research_connectors/youtube/x_search intents"
    )
    assert "[subagent intent=weather" in result
