import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import whatsapp_bot


def _reset_state():
    whatsapp_bot._ACTIVE_REQUESTS.clear()


def test_webhook_replies_busy_instead_of_queuing_duplicate_chat_request():
    _reset_state()
    whatsapp_bot.harness_run = lambda **kwargs: "should not run"

    with TestClient(whatsapp_bot.app) as client:
        whatsapp_bot._ACTIVE_REQUESTS.add("whatsapp_sender")
        response = client.post(
            "/webhook",
            json={"sender": "sender", "message": "second request"},
        )

    assert response.status_code == 200
    assert "still processing" in response.json()["reply"].lower()
    _reset_state()


def test_webhook_returns_user_visible_reply_when_harness_times_out():
    _reset_state()

    async def _timeout(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise asyncio.TimeoutError

    with patch.object(whatsapp_bot, "harness_run", return_value="late result"), \
         patch.object(whatsapp_bot.asyncio, "wait_for", side_effect=_timeout):
        with TestClient(whatsapp_bot.app) as client:
            response = client.post(
                "/webhook",
                json={
                    "sender": "sender",
                    "message": "slow request",
                    "context": "[B] prior message",
                    "fresh_context": True,
                },
            )

    assert response.status_code == 200
    assert "finish" in response.json()["reply"].lower()
    _reset_state()
