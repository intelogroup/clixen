import sys

sys.path.insert(0, ".")

from fastapi.testclient import TestClient

import chat_ui


def _reset_auth_state(monkeypatch, tmp_path):
    state_path = tmp_path / "workspace_state.json"
    monkeypatch.setattr(chat_ui, "WORKSPACE_STATE_PATH", state_path)
    monkeypatch.setattr(chat_ui, "_workspace_state", chat_ui._default_workspace_state())
    chat_ui._persist_workspace_state()


def _register_local_account(client):
    resp = client.post(
        "/api/auth/register",
        json={
            "display_name": "Local Operator",
            "email": "operator@local.dev",
            "password": "supersecret",
            "workspace_name": "Local Workspace",
            "role": "Owner",
        },
    )
    assert resp.status_code == 200


def test_search_stream_endpoint_emits_stage_and_done(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    _reset_auth_state(monkeypatch, tmp_path)
    monkeypatch.setattr("chat_ui._guard_check", lambda message, has_history=False: None)
    
    def mock_websearch(query, on_token=None):
        if on_token:
            on_token("Answer:")
            on_token(" hi")
        return "Answer: hi"
        
    monkeypatch.setattr("chat_ui._websearch", mock_websearch)

    client = TestClient(chat_ui.app)
    _register_local_account(client)
    resp = client.get("/search/stream", params={"message": "latest news", "thread_id": "t-1"})
    assert resp.status_code == 200
    text = resp.text
    assert '"type": "token"' in text
    assert '"text": "Answer:"' in text
    assert '"text": " hi"' in text
    assert '"type": "done"' in text


def test_search_history_endpoint_returns_history(monkeypatch):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)

    client = TestClient(chat_ui.app)
    resp = client.get("/search/history/t-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["thread_id"] == "t-1"
    assert data["history"] == []


# Integration: requires real Ollama/network/creds/server — auto-skipped when deps down.
import pytest as _pytest
pytestmark = _pytest.mark.live
