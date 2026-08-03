import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from fastapi.testclient import TestClient

import chat_ui


def _patch_workflow_db(monkeypatch, tmp_path):
    db_path = tmp_path / "workflow_state.db"
    monkeypatch.setattr("chat_ui.workflow_store.DB_PATH", db_path)
    chat_ui.workflow_store.init()
    return db_path


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
    return resp


def test_chat_endpoint_returns_reply_model_and_intent(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    monkeypatch.setattr("chat_ui.harness_run", lambda message, tools=None, model=None, chat_id=None, **kwargs: ("hello back", "mistral-nemo", "casual"))
    _reset_auth_state(monkeypatch, tmp_path)

    client = TestClient(chat_ui.app)
    _register_local_account(client)
    resp = client.post("/chat", json={"message": "hello", "chat_id": "t-1"})

    assert resp.status_code == 200
    assert resp.json() == {"reply": "hello back", "model": "mistral-nemo", "intent": "casual"}


def test_chat_stream_endpoint_emits_tokens_and_done(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    _reset_auth_state(monkeypatch, tmp_path)

    def _fake_run(
        message,
        chat_id=None,
        on_token=None,
        project_root=None,
        model=None,
        force_web_search=False,
        force_local_agent=False,
        images=None,
        **kwargs,
    ):
        on_token("Hello")
        on_token(" world")
        return "Hello world", "mistral-nemo", "casual"

    monkeypatch.setattr("chat_ui.harness_run", _fake_run)

    client = TestClient(chat_ui.app)
    _register_local_account(client)
    resp = client.get("/chat/stream", params={"message": "hello", "chat_id": "t-1"})

    assert resp.status_code == 200
    text = resp.text
    assert '"token": "Hello"' in text
    assert '"token": " world"' in text
    assert '"done": true' in text
    assert '"model": "mistral-nemo"' in text
    assert '"intent": "casual"' in text


def test_chat_stream_local_agent_reads_file_without_model(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    _reset_auth_state(monkeypatch, tmp_path)

    from langchain_core.messages import HumanMessage

    def _fake_harness_run(
        message, chat_id=None, on_token=None, project_root=None,
        model=None, force_web_search=False, force_local_agent=False,
        **kwargs,
    ):
        if on_token:
            on_token(HumanMessage(content="The secret word is zircon."))
        return "The secret word is zircon.", "gemma4", "filesystem"

    monkeypatch.setattr("chat_ui.harness_run", _fake_harness_run)

    target = tmp_path / "alpha.txt"
    target.write_text("secret_word=zircon\n", encoding="utf-8")

    client = TestClient(chat_ui.app)
    _register_local_account(client)
    resp = client.get(
        "/chat/stream",
        params={
            "message": f"Read {target} and tell me the secret word.",
            "chat_id": "t-local",
            "local_agent": "1",
        },
    )

    assert resp.status_code == 200
    text = resp.text
    assert "zircon" in text
    assert '"done": true' in text
    assert '"intent": "filesystem"' in text


def test_tts_speak_returns_wav(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    monkeypatch.setattr("chat_ui._synthesize", lambda text, voice="af_heart": b"RIFFtestwav")
    _reset_auth_state(monkeypatch, tmp_path)

    client = TestClient(chat_ui.app)
    _register_local_account(client)
    resp = client.post("/tts/speak", json={"text": "hello", "voice": "af_heart"})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content == b"RIFFtestwav"


def test_tts_speak_stream_emits_audio_chunks(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    monkeypatch.setattr(
        "chat_ui._synthesize_cached",
        lambda text, voice="af_heart": f"wav:{text}".encode("utf-8"),
    )
    _reset_auth_state(monkeypatch, tmp_path)

    client = TestClient(chat_ui.app)
    _register_local_account(client)
    resp = client.post(
        "/tts/speak/stream",
        json={"text": "Hello world. Second sentence!", "voice": "af_heart"},
    )

    assert resp.status_code == 200
    text = resp.text
    assert '"type": "audio"' in text
    assert "d2F2OkhlbGxvIHdvcmxkLg==" in text
    assert "d2F2OlNlY29uZCBzZW50ZW5jZSE=" in text
    assert '"type": "done"' in text


def test_workspace_payload_includes_gmail_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    _reset_auth_state(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "chat_ui._gmail_connection_snapshot",
        lambda: {
            "connected": True,
            "token_path": "/tmp/gmail_token.json",
            "credentials_path": "/tmp/google_credentials.json",
            "detail": "Existing Gmail token detected",
        },
    )
    monkeypatch.setattr(
        "chat_ui._whatsapp_connection_snapshot",
        lambda: {"connected": False, "detail": "Not connected"},
    )
    client = TestClient(chat_ui.app)
    _register_local_account(client)

    resp = client.get("/api/workspace")

    assert resp.status_code == 200
    data = resp.json()
    assert data["integrations"]["gmail"]["connected"] is True
    assert data["integrations"]["gmail"]["token_path"] == "/tmp/gmail_token.json"
    assert data["integrations"]["whatsapp"]["connected"] is False


def test_local_auth_flow_guards_chat_and_supports_logout(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    monkeypatch.setattr("chat_ui.harness_run", lambda message, tools=None, model=None, chat_id=None, **kwargs: ("ok", "mistral-nemo", "casual"))
    _reset_auth_state(monkeypatch, tmp_path)
    client = TestClient(chat_ui.app)

    # Local loopback client must be auto-authenticated immediately
    resp = client.post("/chat", json={"message": "hello", "chat_id": "t-1"})
    assert resp.status_code == 200
    assert resp.json() == {"reply": "ok", "model": "mistral-nemo", "intent": "casual"}

    workspace = client.get("/api/workspace")
    assert workspace.status_code == 200
    assert workspace.json()["auth"]["has_account"] is True
    assert workspace.json()["user"]["authenticated"] is True


def test_health_ollama_reports_ok_when_api_tags_respond(monkeypatch):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)

    class _Resp:
        ok = True

        def json(self):
            return {"models": [{"name": "qwen3:4b"}, {"name": "gemma4:latest"}]}

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: _Resp())

    client = TestClient(chat_ui.app)
    resp = client.get("/health/ollama")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "models": ["qwen3:4b", "gemma4"]}


def test_health_ollama_falls_back_to_curl_when_requests_fails(monkeypatch):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)

    def _boom(*args, **kwargs):
        raise RuntimeError("socket blocked")

    monkeypatch.setattr("requests.get", _boom)
    monkeypatch.setattr(
        "chat_ui.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout='{"models":[{"name":"qwen3.5:2b"},{"name":"gemma4:latest"}]}'
        ),
    )

    client = TestClient(chat_ui.app)
    resp = client.get("/health/ollama")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "models": ["qwen3.5:2b", "gemma4"]}


def test_health_ollama_reports_blocked_when_localhost_access_is_denied(monkeypatch):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)

    def _boom(*args, **kwargs):
        raise RuntimeError("connect: operation not permitted")

    monkeypatch.setattr("requests.get", _boom)
    monkeypatch.setattr("chat_ui.subprocess.run", _boom)

    client = TestClient(chat_ui.app)
    resp = client.get("/health/ollama")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "blocked"
    assert "cannot reach" in data["detail"].lower()


def test_favicon_route_exists(monkeypatch):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    client = TestClient(chat_ui.app)

    resp = client.get("/favicon.ico")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")


def test_workflow_creation_and_listing(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    _patch_workflow_db(monkeypatch, tmp_path)
    client = TestClient(chat_ui.app)

    create = client.post(
        "/workflows",
        json={
            "automation_id": "watch_and_tell",
            "config": {"topic": "GPU prices"},
            "schedule": {"interval_seconds": 3600},
            "dedupe_key": "gpu-prices",
        },
    )

    assert create.status_code == 200
    body = create.json()
    assert body["automation_id"] == "watch_and_tell"
    assert body["task_name"] == "watch_and_alert"
    assert body["config"]["topic"] == "GPU prices"
    assert body["next_run_at"]

    listed = client.get("/workflows")
    assert listed.status_code == 200
    workflows = listed.json()["workflows"]
    assert len(workflows) == 1
    assert workflows[0]["dedupe_key"] == "gpu-prices"


def test_workflow_creation_supports_cron_schedule(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    _patch_workflow_db(monkeypatch, tmp_path)
    client = TestClient(chat_ui.app)

    create = client.post(
        "/workflows",
        json={
            "automation_id": "watch_and_tell",
            "config": {"topic": "earnings calls"},
            "schedule": {"cron": "0 8 * * 1-5", "timezone": "America/New_York"},
        },
    )

    assert create.status_code == 200
    body = create.json()
    assert body["schedule"]["cron"] == "0 8 * * 1-5"
    assert body["schedule"]["timezone"] == "America/New_York"
    assert body["next_run_at"]


def test_workflow_pause_resume_and_run_now(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    _patch_workflow_db(monkeypatch, tmp_path)
    client = TestClient(chat_ui.app)

    created = client.post(
        "/workflows",
        json={
            "automation_id": "watch_and_tell",
            "config": {"topic": "rate cuts"},
            "schedule": {"interval_seconds": 3600},
        },
    ).json()

    paused = client.post(f"/workflows/{created['id']}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/workflows/{created['id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"
    assert resumed.json()["next_run_at"]

    run_now = client.post(f"/workflows/{created['id']}/run-now")
    assert run_now.status_code == 200
    assert run_now.json()["status"] == "active"
    assert run_now.json()["next_run_at"]


def test_approval_creation_emits_persistent_notification(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    _patch_workflow_db(monkeypatch, tmp_path)
    client = TestClient(chat_ui.app)

    create = client.post(
        "/approvals",
        json={
            "kind": "browser_submit",
            "payload": {"url": "https://example.com/form", "summary": "Ready to submit"},
        },
    )

    assert create.status_code == 200
    approval = create.json()
    assert approval["status"] == "pending"
    assert approval["kind"] == "browser_submit"

    approvals = client.get("/approvals")
    assert approvals.status_code == 200
    assert approvals.json()["approvals"][0]["id"] == approval["id"]

    notifications = client.get("/notifications")
    assert notifications.status_code == 200
    notif = notifications.json()["notifications"][0]
    assert notif["action_type"] == "approval"
    assert notif["action_payload"]["approval_id"] == approval["id"]


def test_approval_resolution(monkeypatch, tmp_path):
    monkeypatch.setattr("chat_ui.ollama_warmup", lambda models: None)
    _patch_workflow_db(monkeypatch, tmp_path)
    client = TestClient(chat_ui.app)

    create = client.post("/approvals", json={"kind": "send_email", "payload": {"to": "user@example.com"}})
    approval_id = create.json()["id"]

    approve = client.post(f"/approvals/{approval_id}/approve")
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"
