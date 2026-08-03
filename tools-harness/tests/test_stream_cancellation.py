import threading
import time
import pytest
from clients.cancellation import check_aborted, QueryAbortedException
from clients.ollama_client import _run_streaming

def test_thread_cooperative_cancellation():
    """Assert that check_aborted raises QueryAbortedException when abort_event is set."""
    abort_event = threading.Event()
    raised = False

    def thread_target():
        nonlocal raised
        try:
            for _ in range(100):
                check_aborted()
                time.sleep(0.01)
        except QueryAbortedException:
            raised = True

    t = threading.Thread(target=thread_target)
    t.abort_event = abort_event
    t.start()

    # Wait a bit then set the abort event
    time.sleep(0.05)
    abort_event.set()
    t.join(timeout=1.0)

    assert raised is True
    assert not t.is_alive()

def test_check_aborted_no_event():
    """Assert check_aborted does nothing when no abort_event exists on the thread."""
    # Should not raise any exception
    check_aborted()

class MockOllamaChunk:
    def __init__(self, content):
        self.message = type('Message', (), {'content': content, 'tool_calls': None})()

def test_run_streaming_cancellation(monkeypatch):
    """Assert that _run_streaming stops immediately and raises QueryAbortedException when cancelled."""
    abort_event = threading.Event()
    
    # Mock Ollama Client and its chat method to yield chunks
    def mock_chat(*args, **kwargs):
        for i in range(100):
            yield MockOllamaChunk(f"token_{i}")
            time.sleep(0.01)

    monkeypatch.setattr("clients.ollama_client._get_client", lambda: type('Client', (), {'chat': mock_chat})())

    raised = False
    def run_target():
        nonlocal raised
        try:
            _run_streaming(
                model="gemma4:e2b",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                on_token=lambda t: None
            )
        except QueryAbortedException:
            raised = True

    t = threading.Thread(target=run_target)
    t.abort_event = abort_event
    t.start()

    # Cancel immediately
    time.sleep(0.02)
    abort_event.set()
    t.join(timeout=1.0)

    assert raised is True
    assert not t.is_alive()
