import sys
sys.path.insert(0, ".")
from types import SimpleNamespace
import pytest
import chat_ui
from store import conversation

def test_current_account_local_auth(monkeypatch):
    # Setup mock workspace state
    monkeypatch.setattr(chat_ui, "WORKSPACE_STATE_PATH", "/tmp/non_existent_path_to_state.json")
    monkeypatch.setattr(chat_ui, "_workspace_state", {
        "session_secret": "abc",
        "account": {
            "email": "dev@local.dev",
            "display_name": "Dev",
            "workspace_name": "Dev",
            "role": "Owner",
            "password_salt": "abc",
            "password_hash": "abc"
        }
    })
    monkeypatch.setenv("DEV_EMAIL", "dev@local.dev")

    # Test request with local client (127.0.0.1)
    req_local = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        cookies={}
    )
    account = chat_ui._current_account(req_local)
    assert account is not None
    assert account["email"] == "dev@local.dev"

    # Test request with external client IP (192.168.1.50)
    req_external = SimpleNamespace(
        client=SimpleNamespace(host="192.168.1.50"),
        cookies={}
    )
    account_ext = chat_ui._current_account(req_external)
    assert account_ext is None

    # Test request with FastAPI TestClient host ("testclient")
    req_testclient = SimpleNamespace(
        client=SimpleNamespace(host="testclient"),
        cookies={}
    )
    account_test = chat_ui._current_account(req_testclient)
    assert account_test is not None
    assert account_test["email"] == "dev@local.dev"


def test_conversation_store_lru_eviction(monkeypatch):
    # Patch MAX_CACHE_SIZE to small value for testing
    monkeypatch.setattr(conversation, "MAX_CACHE_SIZE", 3)
    conversation._store.clear()
    conversation._cache_order.clear()

    # Stub file saving
    monkeypatch.setattr(conversation, "_save_to_disk", lambda cid, hist: None)

    # Add 3 conversations
    conversation.append("chat_1", "user", "msg1")
    conversation.append("chat_2", "user", "msg2")
    conversation.append("chat_3", "user", "msg3")
    
    assert len(conversation._store) == 3
    assert "chat_1" in conversation._store

    # Adding 4th should evict chat_1 from memory
    conversation.append("chat_4", "user", "msg4")
    assert len(conversation._store) == 3
    assert "chat_1" not in conversation._store
    assert "chat_4" in conversation._store

    # Mock _load_from_disk to return a dummy list to verify reload works
    monkeypatch.setattr(conversation, "_load_from_disk", lambda cid: [{"role": "user", "content": "reloaded"}])
    res = conversation.get("chat_1")
    assert res == [{"role": "user", "content": "reloaded"}]
    assert "chat_1" in conversation._store
    # chat_2 should now be evicted since chat_1 was touched and chat_2 was the oldest
    assert "chat_2" not in conversation._store


def test_atomic_save_to_disk(tmp_path, monkeypatch):
    # Set session directory to temp path
    monkeypatch.setattr(conversation, "_SESSIONS_DIR", tmp_path)
    
    # Write session
    conversation.append("test_atomic", "user", "hello")
    session_file = conversation._session_path("test_atomic")
    
    # Verify file exists and has correct content
    assert session_file.exists()
    # Ensure no leftover temp files
    temp_files = list(tmp_path.glob("*.tmp"))
    assert len(temp_files) == 0
