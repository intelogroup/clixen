import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import fal


def test_missing_key(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    out = fal.fal_generate("fal-ai/flux/schnell", prompt="a cat")
    assert "[fal error]" in out
    assert "FAL_KEY not set" in out


def test_success_flow(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "uuid:hex")

    def fake_post(url, **kwargs):
        return MagicMock(
            status_code=200,
            json=lambda: {
                "request_id": "req1",
                "status_url": "https://queue.fal.run/fal-ai/flux/schnell/requests/req1/status",
                "response_url": "https://queue.fal.run/fal-ai/flux/schnell/requests/req1",
            },
        )

    def fake_get(url, **kwargs):
        if "status" in url:
            return MagicMock(status_code=200, json=lambda: {"status": "COMPLETED"})
        return MagicMock(
            status_code=200,
            json=lambda: {"images": [{"url": "https://v3.fal.media/files/x.png"}], "prompt": "a cat"},
        )

    with patch("tools.fal.requests.post", side_effect=fake_post), \
         patch("tools.fal.requests.get", side_effect=fake_get), \
         patch("tools.fal.time.sleep", return_value=None):
        out = fal.fal_generate("fal-ai/flux/schnell", prompt="a cat")

    assert "https://v3.fal.media/files/x.png" in out


def test_submit_rejection_surfaces_detail(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "uuid:hex")

    def fake_post(url, **kwargs):
        return MagicMock(
            status_code=402,
            json=lambda: {"detail": "User is locked. Reason: Exhausted balance."},
        )

    with patch("tools.fal.requests.post", side_effect=fake_post):
        out = fal.fal_generate("fal-ai/flux/schnell", prompt="a cat")

    assert "[fal error]" in out
    assert "Exhausted balance" in out


def test_payload_merge(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "uuid:hex")
    seen = {}

    def fake_post(url, **kwargs):
        seen["json"] = kwargs.get("json")
        return MagicMock(
            status_code=200,
            json=lambda: {
                "request_id": "req1",
                "status_url": "https://q/status",
                "response_url": "https://q/response",
            },
        )

    def fake_get(url, **kwargs):
        if "status" in url:
            return MagicMock(status_code=200, json=lambda: {"status": "COMPLETED"})
        return MagicMock(status_code=200, json=lambda: {"images": []})

    with patch("tools.fal.requests.post", side_effect=fake_post), \
         patch("tools.fal.requests.get", side_effect=fake_get), \
         patch("tools.fal.time.sleep", return_value=None):
        fal.fal_generate(
            "fal-ai/flux/schnell",
            prompt="apple",
            input={"loras": ["x"]},
            image_size="square_hd",
            num_images=2,
            seed=42,
        )

    payload = seen["json"]
    assert payload["prompt"] == "apple"
    assert payload["loras"] == ["x"]
    assert payload["image_size"] == "square_hd"
    assert payload["num_images"] == 2
    assert payload["seed"] == 42
