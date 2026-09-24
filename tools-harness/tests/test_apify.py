import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import apify


def test_run_actor_missing_token(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    out = apify.apify_run_actor("apify/website-content-crawler", input={"startUrls": []})
    assert "[apify error]" in out
    assert "APIFY_API_TOKEN not set" in out


def test_run_actor_success_flow(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")

    def fake_post(url, **kwargs):
        return MagicMock(
            status_code=201,
            json=lambda: {"data": {"id": "run1", "defaultDatasetId": "ds1", "status": "READY"}},
        )

    def fake_get(url, **kwargs):
        if "actor-runs" in url:
            return MagicMock(status_code=200, json=lambda: {"data": {"status": "SUCCEEDED"}})
        if "/items" in url:
            return MagicMock(
                status_code=200,
                json=lambda: [{"url": "https://example.com", "title": "Example"}],
            )
        return MagicMock(status_code=200, json=lambda: {})

    with patch("tools.apify.requests.post", side_effect=fake_post), \
         patch("tools.apify.requests.get", side_effect=fake_get), \
         patch("tools.apify.time.sleep", return_value=None):
        out = apify.apify_run_actor("apify/website-content-crawler", input={}, wait_seconds=1)

    assert "SUCCEEDED" not in out or "Example" in out
    assert "https://example.com" in out


def test_run_actor_failed_status(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")

    def fake_post(url, **kwargs):
        return MagicMock(
            status_code=201,
            json=lambda: {"data": {"id": "run1", "defaultDatasetId": "ds1", "status": "FAILED"}},
        )

    with patch("tools.apify.requests.post", side_effect=fake_post):
        out = apify.apify_run_actor("apify/nonexistent", input={})

    assert "[apify error]" in out
    assert "FAILED" in out


def test_list_actors(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")

    def fake_get(url, **kwargs):
        return MagicMock(
            status_code=200,
            json=lambda: {
                "data": {
                    "items": [
                        {"id": "abc", "name": "my-actor", "username": "me", "description": "does stuff"},
                    ]
                }
            },
        )

    with patch("tools.apify.requests.get", side_effect=fake_get):
        out = apify.apify_list_actors()

    assert "abc" in out and "my-actor" in out


def test_run_my_actor_uses_shortcut_id(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs.get("json")
        return MagicMock(
            status_code=201,
            json=lambda: {"data": {"id": "run1", "defaultDatasetId": "ds1", "status": "SUCCEEDED"}},
        )

    def fake_get(url, **kwargs):
        if "actor-runs" in url:
            return MagicMock(status_code=200, json=lambda: {"data": {"status": "SUCCEEDED"}})
        if "/items" in url:
            return MagicMock(status_code=200, json=lambda: [{"echo": True}])
        return MagicMock(status_code=200, json=lambda: {})

    with patch("tools.apify.requests.post", side_effect=fake_post), \
         patch("tools.apify.requests.get", side_effect=fake_get), \
         patch("tools.apify.time.sleep", return_value=None):
        out = apify.apify_run_my_actor(input={"hello": "world"})

    assert apify.MY_ACTOR_ID in seen["url"]
    assert seen["json"]["input"] == {"hello": "world"}
    assert "echo" in out
