"""Route tests for the M2 run endpoints (auth-gated, SSE journal tail)."""
import sys

sys.path.insert(0, ".")

import pytest
from fastapi.testclient import TestClient

import chat_ui
from store import run_store as rs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_ui, "_LOCALHOST_TOKEN", None)
    state_path = tmp_path / "workspace_state.json"
    monkeypatch.setattr(chat_ui, "WORKSPACE_STATE_PATH", state_path)
    monkeypatch.setattr(chat_ui, "_workspace_state", chat_ui._default_workspace_state())
    chat_ui._persist_workspace_state()
    monkeypatch.setattr(rs, "_DB_PATH", tmp_path / "runs_api.sqlite")
    monkeypatch.setattr(rs, "_vault_secret_values", lambda: frozenset())


def _client() -> TestClient:
    return TestClient(chat_ui.app)


def _authed(client) -> TestClient:
    client.post("/api/auth/register", json={"display_name": "Op",
                                            "email": "op@example.com",
                                            "password": "pw-for-tests-1"})
    return client


def test_routes_require_auth(monkeypatch):
    # chat_ui auto-auths LOOPBACK as the single owner, so TestClient is
    # authenticated by design; to prove the gate exists, remove the account.
    c = _client()
    monkeypatch.setattr(chat_ui, "_current_account", lambda request: None)
    assert c.post("/api/runs", json={"goal": "x"}).status_code == 401
    assert c.get("/api/runs/abc/events").status_code == 401
    assert c.post("/api/runs/abc/control", json={"action": "pause"}).status_code == 401


def test_create_run_returns_card_immediately(monkeypatch):
    c = _authed(_client())
    from agents import run_service
    monkeypatch.setattr(run_service, "_default_execute", lambda *a, **k: None)
    resp = c.post("/api/runs", json={"goal": "research X", "tools": ["get_current_time"]})
    assert resp.status_code == 200, resp.text
    card = resp.json()
    assert card["status"] == "queued" and card["goal"] == "research X"
    assert rs.get_run(card["run_id"])["status"] == "queued"


def test_create_run_requires_goal():
    c = _authed(_client())
    assert c.post("/api/runs", json={"goal": "  "}).status_code == 400


def test_events_stream_sends_card_then_journal_frames(monkeypatch):
    c = _authed(_client())
    from agents import run_service
    monkeypatch.setattr(run_service, "_default_execute", lambda *a, **k: None)
    rid = c.post("/api/runs", json={"goal": "g"}).json()["run_id"]
    rs.set_status(rid, "running")
    rs.set_status(rid, "succeeded")
    with c.stream("GET", f"/api/runs/{rid}/events") as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = "".join(r.iter_text())
    assert '"type": "card"' in body
    assert '"type": "done"' in body
    assert f'id: ' in body


def test_events_unknown_run_404():
    c = _authed(_client())
    assert c.get("/api/runs/nope/events").status_code == 404


def test_control_route_journals_intent(monkeypatch):
    c = _authed(_client())
    from agents import run_service
    monkeypatch.setattr(run_service, "_default_execute", lambda *a, **k: None)
    rid = c.post("/api/runs", json={"goal": "g"}).json()["run_id"]
    resp = c.post(f"/api/runs/{rid}/control", json={"action": "steer",
                                                    "text": "focus on pricing"})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert [x["action"] for x in rs.pending_controls(rid)] == ["steer"]


def test_control_route_rejects_bad_action(monkeypatch):
    c = _authed(_client())
    from agents import run_service
    monkeypatch.setattr(run_service, "_default_execute", lambda *a, **k: None)
    rid = c.post("/api/runs", json={"goal": "g"}).json()["run_id"]
    assert c.post(f"/api/runs/{rid}/control", json={"action": "nope"}).status_code == 400


def test_control_on_terminal_run_reports_not_ok(monkeypatch):
    c = _authed(_client())
    from agents import run_service
    monkeypatch.setattr(run_service, "_default_execute", lambda *a, **k: None)
    rid = c.post("/api/runs", json={"goal": "g"}).json()["run_id"]
    rs.set_status(rid, "running")
    rs.set_status(rid, "succeeded")
    resp = c.post(f"/api/runs/{rid}/control", json={"action": "pause"})
    assert resp.json()["ok"] is False
