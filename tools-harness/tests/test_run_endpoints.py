"""Route tests for the run endpoints (auth-gated, SSE journal tail, M6 UI)."""
import sys
from pathlib import Path

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
                                            "password": "test-account-password-placeholder"})
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


def test_budget_summary_is_journal_derived(monkeypatch):
    c = _authed(_client())
    from agents import run_service
    monkeypatch.setattr(run_service, "_default_execute", lambda *a, **k: None)
    rid = c.post("/api/runs", json={"goal": "g", "policy": {"max_rounds": 7}}).json()["run_id"]
    rs.set_status(rid, "running")
    rs.append_event(rid, "round", {"round_idx": 0, "model": "m"})
    rs.append_event(rid, "tool_call", {"id": "t1", "name": "web_search", "args": {}})
    rs.append_event(rid, "tool_result", {"id": "t1", "result": "ok"})
    rs.set_status(rid, "succeeded")
    data = c.get(f"/api/runs/{rid}/budget").json()
    assert data["run_id"] == rid
    assert data["rounds_used"] == 1
    assert data["tool_calls"] == 1
    assert data["max_rounds"] == 7
    assert "tokens" not in data, "never invent token counts we do not track"


def test_runs_page_ships_the_accessibility_contract():
    page = (Path(__file__).resolve().parent.parent / "static" / "runs.html").read_text()
    # a11y contract (plan M6 exit criteria)
    assert 'aria-live="polite"' in page
    assert 'role="status"' in page
    assert "prefers-reduced-motion" in page
    assert 'tabindex="0"' in page, "controls must be keyboard reachable"
    assert "keydown" in page, "keyboard path for pause/kill/steer"
    # status is never color-alone
    assert "Running" in page and "Failed" in page and "Paused" in page
    # screenshot alt text comes from the step description
    assert 'setAttribute("alt"' in page


def test_process_mode_spawns_a_supervised_child(monkeypatch):
    c = _authed(_client())
    spawned = {}

    def fake_spawn(run_id, **kw):
        spawned["run_id"] = run_id
        return object()

    monkeypatch.setattr("jobs.run_supervisor.spawn_run", fake_spawn)
    resp = c.post("/api/runs", json={"goal": "long task", "mode": "process"})
    assert resp.status_code == 200, resp.text
    assert spawned["run_id"] == resp.json()["run_id"]
