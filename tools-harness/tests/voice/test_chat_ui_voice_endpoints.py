"""The mic toggle the orb uses must be a real toggle, over the real API.

`CLIXEN_VOICE_MIC=0` semantics are exercised by patching the module flag, so no
audio device is opened in tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HARNESS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HARNESS))


@pytest.fixture(scope="module")
def client():
    import chat_ui

    chat_ui._VOICE_MIC = False          # never open a device in the test run
    with TestClient(chat_ui.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _disable_localhost_token_gate(monkeypatch):
    """Same bypass test_chat_ui_endpoints uses: no per-install token in tests."""
    import chat_ui

    monkeypatch.setattr(chat_ui, "_LOCALHOST_TOKEN", None)


def test_mic_toggles_on_and_off(client):
    on = client.post("/voice/listen", json={"on": True, "mic": False})
    assert on.status_code == 200
    body = on.json()
    assert body["ok"] is True and body["listening"] is True
    assert body["state"] == "listening"

    status = client.get("/voice/session").json()
    assert status["listening"] is True

    off = client.post("/voice/listen", json={"on": False})
    assert off.json()["listening"] is False
    assert client.get("/voice/session").json()["state"] == "idle"


def test_toggle_is_idempotent(client):
    assert client.post("/voice/listen", json={"on": True, "mic": False}).json()["listening"] is True
    assert client.post("/voice/listen", json={"on": True, "mic": False}).json()["listening"] is True
    assert client.post("/voice/listen", json={"on": False}).json()["listening"] is False
    assert client.post("/voice/listen", json={"on": False}).json()["listening"] is False


def test_feed_accepts_pcm_and_rejects_empty(client):
    client.post("/voice/listen", json={"on": True, "mic": False})
    ok = client.post("/voice/feed", content=b"\x00" * 960)
    assert ok.status_code == 200 and ok.json()["bytes"] == 960
    assert client.post("/voice/feed", content=b"").status_code == 400
    client.post("/voice/listen", json={"on": False})


def test_events_bus_fans_out_published_payloads(client):
    import chat_ui

    q = chat_ui._VOICE_BUS.subscribe()
    try:
        chat_ui._VOICE_BUS.publish({"type": "partial", "text": "hello wor"})
        assert q.get_nowait()["text"] == "hello wor"
    finally:
        chat_ui._VOICE_BUS.unsubscribe(q)
