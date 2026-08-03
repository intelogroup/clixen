"""
Stress tests for SSE streaming pipeline: /chat/stream, /search/stream, /voice/process.

Covers: concurrent connections, rapid sequential sends, cancellation mid-stream,
done-event reliability, cross-stream isolation, tool-loop abort, and error propagation.
"""
import json
import os
import sys
import threading
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
os.environ["DEV_EMAIL"] = "stress@test.com"
os.environ["DEV_PASSWORD"] = "stress-pw"

from fastapi.testclient import TestClient
from chat_ui import (
    app, _workspace_state, _dev_auto_auth_setup, _hash_password,
    _active_streams, _active_streams_lock, abort_active_stream, register_active_stream,
)
from clients.cancellation import QueryAbortedException


# ── Session-wide isolation: never touch the real workspace_state.json ──────
# The stream endpoints persist lifecycle state on success, so without this the
# tests would overwrite the production account with the stress fixture account.

@pytest.fixture(scope="session", autouse=True)
def _isolate_workspace_state(tmp_path_factory):
    import chat_ui
    iso_path = tmp_path_factory.mktemp("ws") / "workspace_state.json"
    chat_ui.WORKSPACE_STATE_PATH = iso_path


# ── Auth fixture (replaces setup_module for isolation) ──────────────────────

@pytest.fixture
def reset_auth():
    """Ensure auth and stream state is clean before each test."""
    os.environ["DEV_EMAIL"] = "stress@test.com"
    os.environ["DEV_PASSWORD"] = "stress-pw"
    _workspace_state.clear()
    salt_hex, password_hash = _hash_password("stress-pw")
    _workspace_state["account"] = {
        "email": "stress@test.com",
        "display_name": "Stress User",
        "role": "Owner",
        "workspace_name": "Stress Workspace",
        "avatar": "S",
        "password_salt": salt_hex,
        "password_hash": password_hash,
    }
    _workspace_state["lifecycle"] = {"first_message_sent": False}
    _dev_auto_auth_setup()
    with _active_streams_lock:
        _active_streams.clear()
    yield
    # Teardown: only clean stream registry, leave workspace state for other modules
    with _active_streams_lock:
        _active_streams.clear()


def _read_sse_events(response) -> list[dict]:
    """Parse SSE response into list of event dicts. Skips pings."""
    events = []
    for raw in response.iter_lines():
        if raw.startswith("data: "):
            try:
                payload = json.loads(raw[6:])
                if payload.get("type") != "ping":
                    events.append(payload)
            except json.JSONDecodeError:
                pass
    return events


def _mock_harness_ok(query, chat_id=None, on_token=None, **kw):
    """harness_run that returns success with a few tokens."""
    on_token("hello")
    on_token(" world")
    return ("hello world", "gemma4:12b-mlx", "casual")


def _mock_websearch_ok(query, on_token=None):
    """websearch that streams tokens and returns answer."""
    on_token("search result")
    return "final answer"


# ═══════════════════════════════════════════════════════════════════
# 1. DONE EVENT RELIABILITY
# ═══════════════════════════════════════════════════════════════════

def test_done_event_on_happy_path(reset_auth):
    """Every completed stream must end with a done event."""
    client = TestClient(app)
    with patch("chat_ui.harness_run", side_effect=_mock_harness_ok):
        res = client.get("/chat/stream?message=hi&chat_id=done-happy")
        events = _read_sse_events(res)
    has_done = any(e.get("done") for e in events)
    tokens = [e for e in events if e.get("token")]
    assert len(tokens) >= 1, f"no tokens in {events}"
    assert has_done, f"missing done in {events}"


def test_done_event_on_error(reset_auth):
    """When harness_run raises, stream must still yield done with error."""
    client = TestClient(app)
    def mock_err(*a, **kw):
        raise RuntimeError("harness exploded")
    with patch("chat_ui.harness_run", side_effect=mock_err):
        res = client.get("/chat/stream?message=err&chat_id=done-err")
        events = _read_sse_events(res)
    last = events[-1]
    assert last.get("error") == "harness exploded", f"last event: {last}"


# ═══════════════════════════════════════════════════════════════════
# 2. RAPID SEQUENTIAL STREAMS
# ═══════════════════════════════════════════════════════════════════

def test_rapid_sequential_streams(reset_auth):
    """Open and close 10 streams in rapid succession. All must yield done."""
    client = TestClient(app)
    with patch("chat_ui.harness_run", side_effect=_mock_harness_ok):
        for i in range(10):
            res = client.get(f"/chat/stream?message=t&chat_id=seq-{i}")
            events = _read_sse_events(res)
            assert any(e.get("done") for e in events), f"stream {i}: no done"


# ═══════════════════════════════════════════════════════════════════
# 3. CANCELLATION MID-STREAM
# ═══════════════════════════════════════════════════════════════════

def test_cancellation_stops_token_flow(reset_auth):
    """
    When abort_event is set mid-stream, check_aborted raises inside
    harness_run, token flow stops short.
    """
    client = TestClient(app)
    harness_started = threading.Event()
    harness_aborted = threading.Event()

    def mock_harness_cancel(query, chat_id=None, on_token=None, **kw):
        harness_started.set()
        for i in range(500):
            try:
                from clients.cancellation import check_aborted
                check_aborted()
            except QueryAbortedException:
                harness_aborted.set()
                raise
            on_token(f"token_{i}")
            time.sleep(0.01)

    import concurrent.futures
    events_result = []

    with patch("chat_ui.harness_run", side_effect=mock_harness_cancel):
        def read_stream():
            nonlocal events_result
            r = client.get("/chat/stream?message=x&chat_id=cancel-test")
            events_result = _read_sse_events(r)

        bg = threading.Thread(target=read_stream, daemon=True)
        bg.start()

        assert harness_started.wait(timeout=3), "harness never started"
        time.sleep(0.2)  # let a few tokens flow
        abort_active_stream("cancel-test")

        assert harness_aborted.wait(timeout=2), "harness was not aborted"
        bg.join(timeout=3)

    token_count = sum(1 for e in events_result if e.get("token"))
    assert token_count < 500, f"expected <500 tokens, got {token_count}"


# ═══════════════════════════════════════════════════════════════════
# 4. CROSS-STREAM ISOLATION
# ═══════════════════════════════════════════════════════════════════

def test_cross_stream_isolation(reset_auth):
    """
    Cancelling stream A must NOT affect stream B.
    """
    client = TestClient(app)
    stream_a_aborted = threading.Event()
    stream_b_complete = threading.Event()
    events_b = []

    def mock_harness_a(query, chat_id=None, on_token=None, **kw):
        for i in range(200):
            try:
                from clients.cancellation import check_aborted
                check_aborted()
            except QueryAbortedException:
                stream_a_aborted.set()
                raise
            on_token(f"a_{i}")
            time.sleep(0.02)

    def mock_harness_b(query, chat_id=None, on_token=None, **kw):
        for i in range(5):
            on_token(f"b_{i}")
            time.sleep(0.01)
        stream_b_complete.set()
        return ("done B", "gemma4:12b-mlx", "casual")

    def harness_dispatcher(query, chat_id=None, on_token=None, **kw):
        if "stream-a" in (chat_id or ""):
            mock_harness_a(query, chat_id=chat_id, on_token=on_token, **kw)
        else:
            return mock_harness_b(query, chat_id=chat_id, on_token=on_token, **kw)

    import concurrent.futures
    events_a = []

    with patch("chat_ui.harness_run", side_effect=harness_dispatcher):
        def read_a():
            nonlocal events_a
            r = client.get("/chat/stream?message=a&chat_id=stream-a")
            events_a = _read_sse_events(r)
        def read_b():
            nonlocal events_b
            r = client.get("/chat/stream?message=b&chat_id=stream-b")
            events_b = _read_sse_events(r)

        ta = threading.Thread(target=read_a, daemon=True)
        tb = threading.Thread(target=read_b, daemon=True)
        ta.start()
        tb.start()

        time.sleep(0.3)
        abort_active_stream("stream-a")

        assert stream_a_aborted.wait(timeout=3), "stream A was not aborted"
        assert stream_b_complete.wait(timeout=3), "stream B did not complete"
        ta.join(timeout=3)
        tb.join(timeout=3)

    b_tokens = [e for e in events_b if e.get("token")]
    b_done = [e for e in events_b if e.get("done")]
    assert len(b_tokens) == 5, f"stream B: expected 5 tokens, got {len(b_tokens)}"
    assert len(b_done) == 1, f"stream B: missing done event"


# ═══════════════════════════════════════════════════════════════════
# 5. TOOL LOOP ABORT
# ═══════════════════════════════════════════════════════════════════

def test_tool_loop_cancellation_between_rounds(reset_auth):
    """check_aborted() is called in chat() tool loop. Verifies between-round cancel."""
    from clients.ollama_client import chat

    call_count = 0
    abort_event = threading.Event()

    def mock_streaming(model, messages, tools, on_token, options=None):
        nonlocal call_count
        call_count += 1
        time.sleep(0.2)  # simulate LLM latency so abort can arrive between rounds
        if call_count == 1:
            mock_fn = type("Fn", (), {"name": "echo", "arguments": {"msg": "ping"}})()
            mock_tc = type("ToolCall", (), {"function": mock_fn})()
            return ("", [mock_tc], None, type("Msg", (), {"content": "", "tool_calls": [mock_tc]})())
        else:
            return ("done answer", None, None, type("Msg", (), {"content": "done answer", "tool_calls": None})())
    def mock_local(model, messages, tools, temperature=None, options=None):
        nonlocal call_count
        call_count += 1
        msg = type("Msg", (), {"content": "done answer", "tool_calls": None})()
        resp = type("Resp", (), {"message": msg})()
        return resp, {}

    result = None

    def run_chat():
        nonlocal result
        try:
            chat(
                user_message="test",
                model="gemma4:12b-mlx",
                tools=[{"function": {"name": "echo"}}],
                on_token=lambda t: None,
                options={"mock": True},
            )
        except QueryAbortedException:
            result = "aborted"
            return
        result = "completed"

    with patch("clients.ollama_client._run_local", side_effect=mock_local), \
         patch("clients.ollama_client._run_streaming", side_effect=mock_streaming), \
         patch("clients.ollama_client.execute_tool", return_value="pong"):

        t = threading.Thread(target=run_chat)
        t.abort_event = abort_event
        t.start()

        time.sleep(0.1)   # wait for round 1 to start
        abort_event.set()   # cancel before round 2 begins
        t.join(timeout=5)

    assert result == "aborted", f"expected 'aborted', got {result!r}"
    assert call_count == 1, f"should stop after 1 round, got {call_count}"


# ═══════════════════════════════════════════════════════════════════
# 6. ACTIVE STREAM REGISTRY
# ═══════════════════════════════════════════════════════════════════

def test_register_active_stream_aborts_previous(reset_auth):
    """register_active_stream must abort the previous stream for same chat_id."""
    with _active_streams_lock:
        _active_streams.clear()

    ev1 = register_active_stream("chat-x")
    assert not ev1.is_set()

    ev2 = register_active_stream("chat-x")
    assert ev1.is_set(), "ev1 should be set after re-register"
    assert not ev2.is_set()

    ev2.set()
    with _active_streams_lock:
        _active_streams.clear()


def test_register_active_stream_aborts_prefix_keys(reset_auth):
    """register_active_stream also kills keys starting with chat_id_ prefix."""
    with _active_streams_lock:
        _active_streams.clear()

    ev1 = register_active_stream("main", key="main_thread_1")
    assert not ev1.is_set()

    ev2 = register_active_stream("main")
    assert ev1.is_set()
    assert not ev2.is_set()

    ev2.set()
    with _active_streams_lock:
        _active_streams.clear()


# ═══════════════════════════════════════════════════════════════════
# 7. SEARCH STREAM — NORMAL PATH
# ═══════════════════════════════════════════════════════════════════

def test_search_stream_normal(reset_auth):
    """Search stream works end-to-end with mocked websearch."""
    client = TestClient(app)
    def mock_guard(query, has_history=False):
        return ""  # no clarification needed

    with patch("chat_ui._websearch", side_effect=_mock_websearch_ok), \
         patch("chat_ui._guard_check", side_effect=mock_guard):
        res = client.get("/search/stream?message=query&thread_id=search-ok")
        events = _read_sse_events(res)

    tokens = [e for e in events if e.get("type") == "token"]
    done_events = [e for e in events if e.get("type") == "done"]
    assert len(tokens) == 1
    assert len(done_events) == 1
    assert tokens[0]["text"] == "search result"


def test_search_stream_error_handling(reset_auth):
    """When _websearch raises, search stream yields done without crash."""
    client = TestClient(app)
    def mock_broken(query, on_token):
        raise RuntimeError("search backend down")
    def mock_guard(query, has_history=False):
        return ""

    with patch("chat_ui._websearch", side_effect=mock_broken), \
         patch("chat_ui._guard_check", side_effect=mock_guard):
        res = client.get("/search/stream?message=test&thread_id=search-err")
        events = _read_sse_events(res)

    assert len(events) >= 1
    last = events[-1]
    assert last.get("type") == "done" or last.get("done"), f"expected done event, got {last}"


def test_search_stream_clarification(reset_auth):
    """When guard_check returns clarification, stream returns it as done."""
    client = TestClient(app)
    def mock_guard(query, has_history=False):
        return "Please be more specific"

    with patch("chat_ui._guard_check", side_effect=mock_guard):
        res = client.get("/search/stream?message=vague&thread_id=clarity")
        events = _read_sse_events(res)

    assert events[0]["type"] == "done"
    assert "Please be more specific" in events[0]["reply"]


# ═══════════════════════════════════════════════════════════════════
# 8. CONCURRENT STREAMS
# ═══════════════════════════════════════════════════════════════════

def test_concurrent_streams_different_chats(reset_auth):
    """5 concurrent streams with different chat_ids all complete independently."""
    client = TestClient(app)
    harness_called = set()
    lock = threading.Lock()
    stream_results = {}

    def mock_concurrent(query, chat_id=None, on_token=None, **kw):
        with lock:
            harness_called.add(chat_id)
        delay = hash(chat_id) % 3 * 0.03
        time.sleep(delay)
        for j in range(3):
            on_token(f"msg_{chat_id}_{j}")
            time.sleep(0.02)
        return ("done", "gemma4:12b-mlx", "casual")

    with patch("chat_ui.harness_run", side_effect=mock_concurrent):
        def read_stream(cid):
            r = client.get(f"/chat/stream?message=t&chat_id={cid}")
            stream_results[cid] = _read_sse_events(r)

        threads = []
        for i in range(5):
            cid = f"concurrent-{i}"
            t = threading.Thread(target=read_stream, args=(cid,), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=5)
            assert not t.is_alive(), "thread still alive after join"

    assert len(harness_called) == 5
    for i in range(5):
        cid = f"concurrent-{i}"
        assert cid in stream_results, f"missing results for {cid}"
        tokens = [e for e in stream_results[cid] if e.get("token")]
        done = [e for e in stream_results[cid] if e.get("done")]
        assert len(tokens) == 3, f"{cid}: expected 3 tokens, got {len(tokens)}"
        assert len(done) == 1, f"{cid}: missing done event"
