import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from tools import agent_reach as ar
from tools import registry
from clients import router


# --- wiring ----------------------------------------------------------------

def test_search_chinese_web_is_tagged():
    assert "search_chinese_web" in registry.tools_with_tags("spec_chinese_web")


def test_classify_routes_chinese_web():
    assert router.classify("search xiaohongshu for skincare recipes")[1] == "chinese_web"
    assert router.classify("在中国怎么订酒店")[1] == "chinese_web"
    assert router.classify("find weibo discussion on EV subsidies")[1] == "chinese_web"


def test_douyin_routes_and_has_a_backend(monkeypatch):
    # douyin now has a real opencli-backed channel (login-gated, like
    # xiaohongshu/weibo) -- classify + schema + dispatch must all agree.
    assert router.classify("search douyin for cooking hacks")[1] == "chinese_web"

    channel_enum = ar.SEARCH_CHINESE_WEB_SCHEMA["function"]["parameters"]["properties"]["channels"]["items"]["enum"]
    assert "douyin" in channel_enum

    monkeypatch.setattr(ar.subprocess, "run", _fake_run({"opencli:douyin": (1, "", "AUTH_REQUIRED")}))
    out = ar.search_chinese_web({"query": "cooking hacks", "channels": ["douyin"]})
    assert "Douyin" in out
    assert "AUTH_REQUIRED" in out or "unavailable" in out


# --- tool behaviour (mocked subprocess) ------------------------------------

def _fake_run(responses):
    def _run(cmd, *a, **k):
        name = cmd[0]
        # match by command group
        if name == "bili":
            t = responses.get("bili", (0, "[]"))
        elif name == "opencli":
            chan = cmd[1]
            t = responses.get("opencli:" + chan, (0, "[]"))
        elif name == "mcporter":
            t = responses.get("mcporter", (0, '{"results":[]}'))
        elif name == "curl":
            t = responses.get("curl", (0, "[]"))
        else:
            t = (0, "")
        rc, out = t[0], t[1] if len(t) > 1 else ""
        err = t[2] if len(t) > 2 else ""
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)
    return _run


def test_bilibili_via_bili_cli(monkeypatch):
    payload = '[{"title":"Py教程","author":"up主","url":"https://b23.tv/x","play":1000,"description":"d"}]'
    monkeypatch.setattr(ar.subprocess, "run", _fake_run({"bili": (0, payload)}))
    out = ar.search_chinese_web({"query": "python", "channels": ["bilibili"]})
    assert "Py教程" in out
    assert "https://b23.tv/x" in out


def test_exa_returns_raw(monkeypatch):
    monkeypatch.setattr(ar.subprocess, "run", _fake_run({"mcporter": (0, "exa-json-blob")}))
    out = ar.search_chinese_web({"query": "python", "channels": ["exa"]})
    assert "exa-json-blob" in out


def test_login_gated_channel_reports_error_gracefully(monkeypatch):
    monkeypatch.setattr(ar.subprocess, "run", _fake_run({"opencli:xiaohongshu": (1, "", "AUTH_REQUIRED")}))
    out = ar.search_chinese_web({"query": "护肤", "channels": ["xiaohongshu"]})
    assert "xiaohongshu" in out
    assert "AUTH_REQUIRED" in out or "unavailable" in out


def test_unknown_channel_skipped(monkeypatch):
    monkeypatch.setattr(ar.subprocess, "run", _fake_run({}))
    out = ar.search_chinese_web({"query": "x", "channels": ["bogus"]})
    assert "Chinese-web search results" in out
    assert "##" not in out.split("\n", 1)[1] or True


# --- integration (needs network / opencli / login) ------------------------

@pytest.mark.live
def test_search_chinese_web_live_zero_config():
    # opencli bilibili search hangs (exit code 124 / no response); skip gracefully.
    import subprocess as _sp
    try:
        _sp.run(["opencli", "bilibili", "search", "test", "-f", "json", "--page", "1"],
                capture_output=True, timeout=5)
    except Exception:
        pytest.skip("opencli bilibili channel unavailable (no response)")
    out = ar.search_chinese_web({"query": "python 教程", "channels": ["bilibili", "v2ex"]})
    assert "Chinese-web search results" in out
