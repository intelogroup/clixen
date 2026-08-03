"""set_opencode_model — deterministically point OpenCode's serve at a model.

Used to harden voice ("make opencode use the NemoTron model") so the request
goes straight to a config write + serve restart instead of looping through the
generic automation agent (which emitted malformed tool calls and never edited
the file). Resolve friendly aliases ("nemotron", "tron", "zen", "free") to the
canonical OpenCode model spec.
"""
from __future__ import annotations

import os
import re

from tools.opencode_tool import _set_config_model, _restart_serve_for, OPENCODE_CONFIG

PRIMARY = "nvidia/nemotron-3-super-120b-a12b"
FREE = "opencode/nemotron-3-super-free"
DEEPSEEK_FLASH = "opencode/deepseek-v4-flash"
DEEPSEEK_FLASH_FREE = "opencode/deepseek-v4-flash-free"

_ALIASES = {
    "nemotron-3-super-120b": PRIMARY,
    "nemotron": PRIMARY,
    "tron": PRIMARY,
    "nemo": PRIMARY,
    "nemotron super free": FREE,
    "nemotron free": FREE,
    "zen": FREE,
    "deepseek": DEEPSEEK_FLASH,
    "deepseek flash": DEEPSEEK_FLASH,
    "deepseek v4 flash": DEEPSEEK_FLASH,
    "deepseek flash v4": DEEPSEEK_FLASH,
    "deepseek free": DEEPSEEK_FLASH_FREE,
    "deepseek flash free": DEEPSEEK_FLASH_FREE,
    "deepseek v4 flash free": DEEPSEEK_FLASH_FREE,
    "deepseek flash v4 free": DEEPSEEK_FLASH_FREE,
    "deepseek flash v4 free zen": DEEPSEEK_FLASH_FREE,
    "deepseek zen": DEEPSEEK_FLASH_FREE,
}

_KNOWN = {PRIMARY, FREE, DEEPSEEK_FLASH, DEEPSEEK_FLASH_FREE}


def resolve_model(spec: str) -> str:
    s = (spec or "").strip().lower()
    if s in _ALIASES:
        return _ALIASES[s]
    if "deepseek" in s:
        return DEEPSEEK_FLASH_FREE if "free" in s or "zen" in s else DEEPSEEK_FLASH
    if "free" in s and "nemotron" in s:
        return FREE
    if "nemotron" in s or "tron" in s or "nemo" in s:
        return PRIMARY
    return spec.strip()  # pass explicit ids (e.g. nvidia/...) through


SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_opencode_model",
        "description": (
            "Set which model OpenCode's agent (opencode serve) uses, by writing the "
            "top-level `model` in ~/.config/opencode/opencode.json and restarting the "
            "serve process so it picks up the change. Deterministic — no agent loop. "
            "Use for requests like 'make opencode use the NemoTron model', 'switch "
            "opencode to the free nemotron', 'switch to deepseek flash v4 free zen', "
            "or 'set opencode model to <id>'. "
            "Accepts friendly aliases: 'nemotron'/'tron' -> NVIDIA Nemotron 3 Super "
            "120B; 'nemotron free'/'zen'/'free' -> OpenCode Zen Nemotron 3 Super Free; "
            "'deepseek'/'deepseek flash' -> OpenCode Zen DeepSeek V4 Flash; "
            "'deepseek free'/'deepseek flash v4 free zen' -> OpenCode Zen DeepSeek V4 Flash Free."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": (
                        "Model to use. Friendly alias ('nemotron', 'tron', 'zen', "
                        "'free', 'nemotron free', 'deepseek', 'deepseek free') or an "
                        "explicit OpenCode model spec (e.g. "
                        "'nvidia/nemotron-3-super-120b-a12b', "
                        "'opencode/nemotron-3-super-free', "
                        "'opencode/deepseek-v4-flash-free')."
                    ),
                },
            },
            "required": ["model"],
        },
    },
}


def execute(model: str) -> str:
    canonical = resolve_model(model)
    if not canonical:
        return "[set_opencode_model] no model specified"
    try:
        _restart_serve_for(canonical)
    except Exception as e:
        # even if serve restart fails, the config is written and picked up next spawn
        _set_config_model(canonical)
        return (
            f"[set_opencode_model] wrote model='{canonical}' to {OPENCODE_CONFIG}, "
            f"but serve restart failed: {e}. Next `opencode serve` spawn will use it."
        )
    return f"[set_opencode_model] OpenCode model set to '{canonical}' (serve restarted)."
