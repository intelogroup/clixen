import pytest
import time
from unittest.mock import patch, MagicMock
from store import conversation


@pytest.fixture(autouse=True)
def clean_store_and_sessions(tmp_path, monkeypatch):
    """Isolate store and session directory for each test."""
    monkeypatch.setattr(conversation, "_store", {})
    monkeypatch.setattr(conversation, "_locks", {})
    monkeypatch.setattr(conversation, "_cache_order", [])
    monkeypatch.setattr(conversation, "_summary_cache", {})

    # Override session directory to a temp path
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(conversation, "_SESSIONS_DIR", session_dir)
    # _SUMMARY_CACHE_PATH is bound at import time to the REAL sessions dir —
    # monkeypatching _SESSIONS_DIR does NOT redirect it, so _save_summary_cache
    # would write test entries into the live cache. Redirect it explicitly.
    monkeypatch.setattr(conversation, "_SUMMARY_CACHE_PATH", session_dir / "_summary_cache.json")

    # Fold tests write session summaries via _save_to_session_memory →
    # tools.memory_tools._kb() → the REAL data/memory.lance. Isolate the KB
    # singleton to a temp path so fold tests never pollute live memory.
    from store.knowledge_base import KnowledgeBase
    import tools.memory_tools as _mem
    monkeypatch.setattr(_mem, "_mem_kb", KnowledgeBase(db_path=str(tmp_path / "mem.lance")))

    yield


def test_append_and_get():
    chat_id = "test_chat_123"
    conversation.append(chat_id, "user", "Hello agent")
    conversation.append(chat_id, "assistant", "Hello human")

    history = conversation.get(chat_id)
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "Hello agent"}
    assert history[1] == {"role": "assistant", "content": "Hello human"}

    # Assert persisted on disk
    path = conversation._session_path(chat_id)
    assert path.exists()


def test_trim_to_budget_fits():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there"},
    ]
    # available_history_tokens returns large budget
    with patch("store.conversation.available_history_tokens", return_value=100):
        trimmed = conversation.trim_to_budget(history, "qwen3.5:4b", "ping")
        assert len(trimmed) == 2
        assert trimmed == history


def test_trim_to_budget_never_sends_more_than_recent_window_plus_summary():
    # 13 turns: 3 older + 10 recent (KEEP_RECENT_TURNS=10). No summary has been
    # folded (no chat_id passed / nothing cached), so only the recent window
    # is sent — the old behavior of filling spare budget with raw older turns
    # is gone by design.
    history = [
        {"role": "user", "content": "a" * 40},
        {"role": "assistant", "content": "b" * 40},
        {"role": "user", "content": "c" * 40},
    ] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": str(i % 10) * 8}
        for i in range(10)
    ]
    with patch("store.conversation.available_history_tokens", return_value=1000):
        trimmed = conversation.trim_to_budget(history, "qwen3.5:4b", "pong")
        assert len(trimmed) == 10
        assert trimmed == history[-10:]


def test_trim_to_budget_prepends_rolling_summary_when_present():
    chat_id = "test_chat_summary"
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"}
        for i in range(14)
    ]
    conversation._summary_cache[chat_id] = {
        "summary": "User asked about X, agent explained Y.",
        "compacted_through": 4,
    }
    with patch("store.conversation.available_history_tokens", return_value=1000):
        trimmed = conversation.trim_to_budget(history, "qwen3.5:4b", "ping", chat_id=chat_id)
        assert len(trimmed) == 11  # 1 summary turn + 10 recent
        assert "User asked about X" in trimmed[0]["content"]
        assert trimmed[0]["role"] == "assistant"
        assert trimmed[1:] == history[-10:]


def test_trim_to_budget_ignores_stale_summary_when_history_still_short():
    # A cached summary can outlive the history it was folded from (e.g. after
    # clear()+new short chat reusing a chat_id in a test). If history hasn't
    # exceeded the window yet, no summary should be prepended.
    chat_id = "test_chat_stale_summary"
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    conversation._summary_cache[chat_id] = {"summary": "stale", "compacted_through": 4}
    with patch("store.conversation.available_history_tokens", return_value=1000):
        trimmed = conversation.trim_to_budget(history, "qwen3.5:4b", "ping", chat_id=chat_id)
        assert trimmed == history


def test_trim_to_budget_trims_recent_if_needed():
    history = [
        {"role": "user", "content": "1" * 40},
        {"role": "assistant", "content": "2" * 40},
        {"role": "user", "content": "3" * 40},
        {"role": "assistant", "content": "4" * 40},
        {"role": "assistant", "content": "5" * 40},
    ]
    # Available tokens: 25. Current message: "pong" (1 token). Budget = 24 tokens.
    # Recent turns total 50 tokens, exceeds budget.
    with patch("store.conversation.available_history_tokens", return_value=25):
        trimmed = conversation.trim_to_budget(history, "qwen3.5:4b", "pong")
        # Should trim recent from the beginning to fit within 24 tokens.
        # From the end: turn 5 (10 tok), turn 4 (10 tok) -> 20 tok. Turn 3 would exceed 24.
        assert len(trimmed) == 2
        assert [t["content"] for t in trimmed] == ["4" * 40, "5" * 40]


@patch("store.conversation._call_cloud_chat_raw", return_value="Compacted conversation summary.")
def test_compact_old_turns_folds_slid_out_turns_into_summary(mock_call):
    chat_id = "test_compact_1"
    # 14 turns: exceeds KEEP_RECENT_TURNS=10, so the oldest 4 should get folded.
    for i in range(14):
        role = "user" if i % 2 == 0 else "assistant"
        conversation.append(chat_id, role, f"Message turn {i} containing some content")

    conversation.compact_old_turns(chat_id, "qwen3.5:4b")
    time.sleep(0.2)  # background thread

    # Full raw history must be untouched — this is the whole point of the redesign.
    history = conversation.get(chat_id)
    assert len(history) == 14
    assert history[0]["content"] == "Message turn 0 containing some content"

    cached = conversation._summary_cache[chat_id]
    assert cached["summary"] == "Compacted conversation summary."
    assert cached["compacted_through"] == 4


@patch("store.conversation._call_cloud_chat_raw", return_value="Updated summary.")
def test_compact_old_turns_only_folds_new_slice_on_second_call(mock_call):
    chat_id = "test_compact_incremental"
    for i in range(14):
        role = "user" if i % 2 == 0 else "assistant"
        conversation.append(chat_id, role, f"Message turn {i}")

    with patch("store.conversation.available_history_tokens", return_value=100000):
        conversation.compact_old_turns(chat_id, "qwen3.5:4b")
        time.sleep(0.2)
        assert conversation._summary_cache[chat_id]["compacted_through"] == 4

        # Two more turns arrive, sliding one more pair out of the window.
        conversation.append(chat_id, "user", "Message turn 14")
        conversation.append(chat_id, "assistant", "Message turn 15")
        conversation.compact_old_turns(chat_id, "qwen3.5:4b")
        time.sleep(0.2)

        # Second fold should only have summarized the newly-slid-out slice (turns 4-5),
        # not re-summarized turns 0-5 from scratch. The model only ever sees the new
        # slice (no prior-summary merge asked of it) — a 2026-07-10 redesign after
        # the model was found silently dropping new facts when asked to merge and
        # evict under budget in one shot; Python mechanically prepends new facts to
        # the prior summary instead (see store/conversation.py:352-362).
        args, _ = mock_call.call_args
        user_content = args[1]
        assert "EXISTING SUMMARY" not in user_content
        assert "Message turn 4" in user_content
        assert "Message turn 0" not in user_content
        assert conversation._summary_cache[chat_id]["compacted_through"] == 6
        # The merged summary itself carries the new facts forward regardless.
        assert "Updated summary." in conversation._summary_cache[chat_id]["summary"]



def test_compact_old_turns_skipped_if_under_window():
    chat_id = "test_compact_skip"
    for i in range(3):
        conversation.append(chat_id, "user", "hello")

    conversation.compact_old_turns(chat_id, "qwen3.5:4b")
    assert chat_id not in conversation._summary_cache
    assert len(conversation.get(chat_id)) == 3


def test_keep_recent_turns_is_even():
    # Turns are always appended in user+assistant pairs, so history is always
    # even-length. KEEP_RECENT_TURNS must stay even or the tail slice starts
    # mid-pair (a dangling assistant reply with no paired question).
    assert conversation.KEEP_RECENT_TURNS == 10
    assert conversation.KEEP_RECENT_TURNS % 2 == 0


def test_trim_to_budget_recent_slice_is_pair_aligned():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"}
        for i in range(14)
    ]
    with patch("store.conversation.available_history_tokens", return_value=1000):
        trimmed = conversation.trim_to_budget(history, "qwen3.5:4b", "ping")
        assert len(trimmed) == 10
        assert trimmed[0]["role"] == "user"


@patch("store.conversation._call_cloud_chat_raw", return_value="Summary.")
def test_compaction_uses_dedicated_summary_model(mock_call):
    # Compaction must always summarize via the dedicated cloud summarizer, never
    # the live request's model, so it never competes with the busy primary model
    # — the live model is passed into compact_old_turns() but never reaches the
    # summarizer call at all (_call_cloud_chat_raw hardcodes DEFAULT_CLOUD_MODEL).
    chat_id = "test_compact_model"
    for i in range(14):
        role = "user" if i % 2 == 0 else "assistant"
        conversation.append(chat_id, role, f"Message turn {i} containing some content")

    conversation.compact_old_turns(chat_id, "gemma4:12b-mlx")
    time.sleep(0.2)

    assert mock_call.called
    assert conversation._summary_cache[chat_id]["summary"] == "Summary."


@patch("store.conversation._call_cloud_chat_raw", return_value="New facts.")
def test_fold_merges_prior_summary_instead_of_flattening(mock_call):
    # The model extracts NEW facts only (never asked to merge/evict in-prompt —
    # that failed live, see store/conversation.py:352-362); Python mechanically
    # prepends them to prior_summary so new facts always survive later truncation.
    cid = "test_compact_merge"
    prior_summary = "user asked about X, agent explained Y."
    new_slice = [{"role": "user", "content": "follow up question"}]

    conversation._fold_new_turns_into_summary(cid, prior_summary, new_slice, compacted_through=5)

    args, _ = mock_call.call_args
    system, user_content = args
    assert "EXISTING SUMMARY" not in user_content
    assert "user asked about X" not in user_content
    assert "follow up question" in user_content
    assert conversation._summary_cache[cid]["summary"] == "New facts.; user asked about X, agent explained Y."
    assert conversation._summary_cache[cid]["compacted_through"] == 5


def test_call_cloud_chat_raw_uses_cloud_not_ollama():
    # Regression test for a real, confirmed-live issue (2026-07-08): the
    # summarizer used local Ollama, but the tuned homebrew LaunchAgent
    # (OLLAMA_MAX_LOADED_MODELS / KV cache quant) wasn't the instance actually
    # serving — Ollama.app's own launchd job was — causing repeated 60s
    # timeouts under concurrent Telegram/WhatsApp load and silently degrading
    # chat memory to "[Earlier conversation omitted]". Summarization now goes
    # through the cloud client instead of depending on local Ollama health.
    fake_choice = MagicMock()
    fake_choice.message.content = "Cloud summary."

    with patch("clients.cloud_client.raw_completion", return_value=fake_choice) as mock_raw:
        result = conversation._call_cloud_chat_raw("system prompt", "user content")

    assert result == "Cloud summary."
    assert mock_raw.called
    _, kwargs = mock_raw.call_args
    assert kwargs["tools"] == []
    assert kwargs["bypass_budget"] is True
    assert kwargs["messages"][0] == {"role": "system", "content": "system prompt"}
    assert kwargs["messages"][1] == {"role": "user", "content": "user content"}


def test_call_cloud_chat_raw_skips_oversized_input_straight_to_fallback():
    # Regression test (2026-07-14): a fold backlog padded with large tool-call
    # payloads (e.g. write_file dumping a multi-KB document into history) can
    # push the fold input past _FOLD_MODEL's 32k-token cap on its own,
    # guaranteeing a failed call every time before falling back. Oversized
    # input should skip _FOLD_MODEL entirely and go straight to the fallback.
    fake_choice = MagicMock()
    fake_choice.message.content = "Fallback summary."
    huge_content = "x" * 200_000

    with patch("clients.cloud_client.raw_completion", return_value=fake_choice) as mock_raw:
        result = conversation._call_cloud_chat_raw("system prompt", huge_content)

    assert result == "Fallback summary."
    assert mock_raw.call_count == 1  # never attempted _FOLD_MODEL
    _, kwargs = mock_raw.call_args
    from clients.cloud_client import DEFAULT_CLOUD_MODEL
    assert kwargs["model"] == DEFAULT_CLOUD_MODEL


def test_clear_removes_from_memory_disk_and_summary_cache():
    chat_id = "test_clear_me"
    conversation.append(chat_id, "user", "hello")
    conversation.append(chat_id, "assistant", "hi")
    conversation._summary_cache[chat_id] = {"summary": "x", "compacted_through": 2}
    path = conversation._session_path(chat_id)
    assert path.exists()

    conversation.clear(chat_id)

    assert conversation.get(chat_id) == []
    assert not path.exists()
    assert chat_id not in conversation._summary_cache


def test_expire_old_raw_deletes_dormant_keeps_fresh_and_summary(clean_store_and_sessions, tmp_path):
    chat_id = "ttl_old"
    conversation.append(chat_id, "user", "old message")
    path = conversation._session_path(chat_id)
    assert path.exists()

    old = time.time() - 2 * 24 * 3600  # 48h stale
    import os
    os.utime(path, (old, old))
    # Evict from in-memory store — a dormant chat that LRU dropped but kept on disk.
    conversation._store.pop(chat_id, None)

    # A fresh chat + the summary cache must survive.
    fresh = conversation._SESSIONS_DIR / "fresh_hash.json"
    fresh.write_text("[]")
    summary_file = conversation._SESSIONS_DIR / "_summary_cache.json"
    summary_file.write_text("{}")

    deleted = conversation.expire_old_raw()

    assert deleted == 1
    assert not path.exists()
    assert fresh.exists()
    assert summary_file.exists()


def test_expire_old_raw_skips_in_memory_chat(clean_store_and_sessions, tmp_path):
    chat_id = "ttl_active"
    conversation.append(chat_id, "user", "hello")
    path = conversation._session_path(chat_id)
    old = time.time() - 2 * 24 * 3600
    import os
    os.utime(path, (old, old))

    deleted = conversation.expire_old_raw()

    assert deleted == 0
    assert path.exists()
