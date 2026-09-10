"""
Regression test: agents/specialists/dispatch.py's dispatch() must hand the model
string to a specialist unchanged, cloud or local.

History, because this file used to assert the exact opposite. Specialists each
called ollama.chat(model=model, ...) directly with no cloud fallback, so a
cloud-first routed_model ("deepseek/deepseek-v4-flash", DeepSeek-direct with no
"openrouter/" prefix) 404'd against local Ollama — 19 occurrences in
telegram_bot.log before dispatch() was taught to clamp cloud models down to the
local default (2026-07-14).

The clamp became wrong on 2026-09-10, when all nine specialists moved onto
agents/specialists/_llm_step.chat_step(), which branches on
cloud_client.is_cloud_model() itself. Clamping now would force every specialist
onto local Ollama even when it is down — which is what prompted the move.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import agents.specialists.dispatch as dispatch


def _capture_model(model: str) -> str:
    captured = {}

    def fake_specialist(query, model, known_path=None):
        captured["model"] = model
        return object(), {}

    with patch.dict(dispatch._DISPATCHERS, {"read": fake_specialist}):
        dispatch.dispatch("read this file", model=model, specialist_hint="read")
    return captured["model"]


def test_dispatch_passes_cloud_model_through():
    assert _capture_model("deepseek/deepseek-v4-flash") == "deepseek/deepseek-v4-flash"


def test_dispatch_passes_openrouter_model_through():
    model = "openrouter/anthropic/claude-haiku-4.5"
    assert _capture_model(model) == model


def test_dispatch_leaves_local_model_untouched():
    assert _capture_model("gemma4:12b") == "gemma4:12b"
