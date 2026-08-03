"""
Regression test: agents/specialists/dispatch.py's dispatch() must clamp a
cloud model string to the local default before handing it to a specialist.

Specialists call ollama.chat(model=model, ...) directly (a local-only fast
ReAct loop, no cloud_client fallback) — when cloud-first routing passes
routed_model straight through unchanged (e.g. "deepseek/deepseek-v4-flash",
DeepSeek-direct, no "openrouter/" prefix), it 404s against local Ollama since
no local model has that name. Confirmed live in telegram_bot.log, 19
occurrences before the fix (2026-07-14).
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import agents.specialists.dispatch as dispatch


def test_dispatch_clamps_cloud_model_to_local_default():
    captured = {}

    def fake_specialist(query, model, known_path=None):
        captured["model"] = model
        return object(), {}

    with patch.dict(dispatch._DISPATCHERS, {"read": fake_specialist}):
        dispatch.dispatch("read this file", model="deepseek/deepseek-v4-flash", specialist_hint="read")

    assert captured["model"] == "gemma4:12b-mlx"


def test_dispatch_clamps_openrouter_model_to_local_default():
    captured = {}

    def fake_specialist(query, model, known_path=None):
        captured["model"] = model
        return object(), {}

    with patch.dict(dispatch._DISPATCHERS, {"read": fake_specialist}):
        dispatch.dispatch("read this file", model="openrouter/anthropic/claude-haiku-4.5", specialist_hint="read")

    assert captured["model"] == "gemma4:12b-mlx"


def test_dispatch_leaves_local_model_untouched():
    captured = {}

    def fake_specialist(query, model, known_path=None):
        captured["model"] = model
        return object(), {}

    with patch.dict(dispatch._DISPATCHERS, {"read": fake_specialist}):
        dispatch.dispatch("read this file", model="gemma4:12b-mlx", specialist_hint="read")

    assert captured["model"] == "gemma4:12b-mlx"


if __name__ == "__main__":
    test_dispatch_clamps_cloud_model_to_local_default()
    test_dispatch_clamps_openrouter_model_to_local_default()
    test_dispatch_leaves_local_model_untouched()
    print("specialist dispatch cloud-clamp tests passed")
