"""
Regression test: cloud_client provider fallback must RECOVER, not bench a
provider for a day.

Confirmed live (chat_ui.log 2026-08-03): DeepSeek 402'd "Insufficient Balance"
at 11:39 and OpenRouter 402'd "can only afford 7592 tokens" at 11:35/11:43 —
both were marked dead with a flat 24h window (_DEAD_RETRY_AFTER = 86400), so the
whole session silently ran on openai/gpt-4o-mini. A top-up or transient blip
couldn't recover until restart.

Fixes under test:
  - dead-provider window is short (base 300s) and stepped, so the primary is
    re-probed after it expires instead of staying benched for 24h;
  - a successful completion clears the dead mark (half-open probe);
  - an OpenRouter "can only afford N tokens" 402 retries with max_tokens clamped
    under the remaining balance on the SAME provider instead of falling back.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import clients.cloud_client as cc


def _clear_state():
    cc._dead_providers.clear()
    cc._dead_failures.clear()


def test_dead_mark_expires_instead_of_24h():
    _clear_state()
    try:
        cc._mark_dead("deepseek/deepseek-v4-flash")
        assert cc._is_dead("deepseek/deepseek-v4-flash") is True
        assert cc._dead_window("deepseek/") <= 3600  # capped at 1h, not 24h
        # simulate the window elapsing -> provider is re-probed (not dead)
        cc._dead_providers["deepseek/"] = cc.time.time() - cc._DEAD_RETRY_AFTER - 1
        assert cc._is_dead("deepseek/deepseek-v4-flash") is False
    finally:
        _clear_state()


def test_successful_call_clears_dead_mark():
    _clear_state()
    try:
        cc._mark_dead("deepseek/deepseek-v4-flash")
        cc._clear_dead("deepseek/deepseek-v4-flash")
        assert cc._is_dead("deepseek/deepseek-v4-flash") is False
    finally:
        _clear_state()


def test_dead_window_steps_up_on_repeat_failures():
    _clear_state()
    try:
        cc._mark_dead("openrouter/google/gemini-2.5-flash-lite")
        w1 = cc._dead_window("openrouter/")
        cc._mark_dead("openrouter/google/gemini-2.5-flash-lite")
        w2 = cc._dead_window("openrouter/")
        assert w2 > w1
        assert w1 <= 3600 and w2 <= 3600
    finally:
        _clear_state()


def test_low_balance_402_clamps_max_tokens():
    _clear_state()
    try:
        err = Exception(
            "Error code: 402 - {'error': {'message': 'This request requires more "
            "credits, or fewer max_tokens. You requested up to 65535 tokens, but can "
            "only afford 7592. To increase, visit https://openrouter.ai/settings/"
            "credits and add more credits', 'code': 402}}"
        )
        out = cc._clamp_max_tokens_for_afford({"model": "x", "max_tokens": 8192}, err)
        assert out is not None
        assert 256 <= out["max_tokens"] < 7592  # clamped under the affordable ceiling

        # already under the ceiling -> no retry possible
        assert cc._clamp_max_tokens_for_afford({"model": "x", "max_tokens": 5000}, err) is None
        # non-402 / no afford info -> no clamp
        assert cc._clamp_max_tokens_for_afford({"model": "x", "max_tokens": 8192}, Exception("boom")) is None
    finally:
        _clear_state()


def test_fallback_transition_emits_user_notice(monkeypatch):
    _clear_state()
    try:
        calls = {"n": 0}

        def fake_loop(model, messages, tools, on_token, max_rounds, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise Exception("Error code: 402 - Insufficient Balance")
            return "served"

        monkeypatch.setattr(cc, "_run_tool_loop", fake_loop)
        tokens = []
        out = cc.chat(
            "hi", tools=[],
            model="deepseek/deepseek-v4-flash",
            fallback_model="openrouter/google/gemini-2.5-flash-lite",
            on_token=tokens.append, bypass_budget=True,
        )
        assert out == "served"
        assert any(
            "Model fallback" in t and "deepseek" in t and "openrouter" in t for t in tokens
        )
    finally:
        _clear_state()


def test_dead_skip_emits_user_notice(monkeypatch):
    _clear_state()
    try:
        cc._mark_dead("deepseek/deepseek-v4-flash")
        captured = {}

        def fake_loop(model, messages, tools, on_token, max_rounds, **kw):
            captured["model"] = model
            return "served"

        monkeypatch.setattr(cc, "_run_tool_loop", fake_loop)
        tokens = []
        out = cc.chat(
            "hi", tools=[], model="deepseek/deepseek-v4-flash",
            on_token=tokens.append, bypass_budget=True,
        )
        assert out == "served"
        assert captured["model"] == cc.CLOUD_FALLBACK_MODEL  # skipped primary
        assert any("Model fallback" in t and "deepseek" in t for t in tokens)
    finally:
        _clear_state()


if __name__ == "__main__":
    test_dead_mark_expires_instead_of_24h()
    test_successful_call_clears_dead_mark()
    test_dead_window_steps_up_on_repeat_failures()
    test_low_balance_402_clamps_max_tokens()
    print("cloud_client fallback-recovery tests passed")
