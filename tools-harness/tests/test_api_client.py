"""
Test api_client — token roundtrip, HTTP fetch, 401 auto-refresh.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import tools.api_client as ac
from tools.api_client import (
    _save_token,
    _load_token,
    api_fetch,
    api_list_domains,
)

# ── fixtures ──

@pytest.fixture
def tmp_paths(tmp_path):
    ac._TOKENS_DIR = tmp_path / "tokens"
    ac._CONFIGS_DIR = tmp_path / "configs"
    ac._DATA_DIR = tmp_path / "data"
    ac._KEY_FILE = tmp_path / "tokens" / ".fernet_key"
    return tmp_path


def _config():
    return {
        "auth": {
            "type": "cookie",
            "cookie_name": "test-session",
            "refresh_url": "https://example.com/login",
            "refresh_flow": "browseros_login",
        },
        "endpoints": {"orders": {"url": "https://api.example.com/orders", "method": "GET"}},
        "rate_limit": {"min_delay_s": 0},
    }


# ── token tests ──

def test_token_roundtrip(tmp_paths):
    data = {"cookie": "abc123", "refreshed_at": 1000.0}
    _save_token("testdomain", data)
    assert _load_token("testdomain") == data


def test_token_missing(tmp_paths):
    assert _load_token("nonexistent") is None


# ── api_fetch tests ──

def _mock_client(resp):
    """Httpx Client mock. MagicMock __enter__ does NOT return self."""
    m = MagicMock()
    m.get.return_value = resp
    m.__enter__.return_value = m
    return m


def test_api_fetch_success(tmp_paths):
    ac._load_config = lambda d: _config()
    ac._load_token = lambda d: {"cookie": "tok"}
    ac._save_data = lambda d, e, data: str(tmp_paths / "data.json")

    resp = MagicMock(status_code=200)
    resp.json.return_value = {"orders": [{"id": 1}]}

    with patch.object(ac.httpx, "Client", return_value=_mock_client(resp)):
        result = json.loads(api_fetch("test", "orders"))
    assert result == {"orders": [{"id": 1}]}


def test_api_fetch_401_triggers_refresh(tmp_paths):
    ac._load_config = lambda d: _config()
    ac._save_data = lambda d, e, data: str(tmp_paths / "data.json")

    resp_401 = MagicMock(status_code=401, text="unauthorized")
    resp_200 = MagicMock(status_code=200)
    resp_200.json.return_value = {"orders": [{"id": 2}]}

    client = _mock_client(resp_401)
    client.get.side_effect = [resp_401, resp_200]

    ac._REFRESH_HANDLERS = {
        "browseros_login": lambda config, old_token: {"cookie": "new_tok", "refreshed_at": 2000.0},
    }

    with patch.object(ac.httpx, "Client", return_value=client):
        result = json.loads(api_fetch("test", "orders"))
    assert result == {"orders": [{"id": 2}]}
    assert len(client.get.call_args_list) == 2
    assert _load_token("test")["cookie"] == "new_tok"


# ── api_list_domains ──

def test_api_list_domains_empty(tmp_paths):
    result = api_list_domains()
    assert "No API domains configured" in result
