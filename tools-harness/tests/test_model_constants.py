"""
Guards for model-constant drift. The stale-constant bug: a comment claimed
gemini-2.5-flash-lite was retired, while three code sites (cloud fallback, fold,
memory-verify) still pointed at it and CLOUD_FALLBACK_MODEL would have 404'd into
the gpt-4o-mini last resort if the claim were true (live check 2026-08-03: model is
available). These tests pin the invariant that the tiers stay consistent and that
every hardcoded model string resolves in the OpenRouter catalog when a key is
present (live-marked so it auto-skips without network/key).
"""

import os

import pytest

from clients import cloud_client
from store.conversation import _FOLD_MODEL
from jobs.handlers.memory_verify import _VERIFY_FALLBACK_MODEL


def _hardcoded_model_strings():
    from clients import router
    ids = {
        cloud_client.DEFAULT_CLOUD_MODEL,
        cloud_client.CLOUD_FALLBACK_MODEL,
        cloud_client.CLOUD_VISION_MODEL,
        cloud_client.OPENAI_FALLBACK_MODEL,
        _FOLD_MODEL,
        _VERIFY_FALLBACK_MODEL,
    }
    # OCR branch hardcodes a dedicated vision model in the classifier.
    ids.add("openrouter/google/gemini-2.5-flash")
    ids.update(router.MODEL_SPECS.keys())
    return {s for s in ids if s}


def test_fallback_fold_verify_use_same_model():
    assert cloud_client.CLOUD_FALLBACK_MODEL == _FOLD_MODEL, (
        "cloud fallback, conversation fold, and memory-verify should share one tier "
        "model — a swap on one site and not the others silently changes behavior"
    )
    assert cloud_client.CLOUD_FALLBACK_MODEL == _VERIFY_FALLBACK_MODEL


@pytest.mark.live
def test_all_hardcoded_models_exist_in_openrouter_catalog():
    key = os.environ.get("OPENROUTER_API_KEY") or _dotenv_key()
    if not key:
        pytest.skip("OPENROUTER_API_KEY not set")
    import json
    import urllib.request

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        catalog = {m["id"] for m in json.load(resp).get("data", [])}

    # Code strings carry the "openrouter/" provider alias; the catalog uses canonical
    # ids ("google/..."). Local Ollama names ("gemma4:12b-mlx") are never in the
    # catalog and have no "/" route form — exclude them.
    route_models = {s for s in _hardcoded_model_strings() if "/" in s}
    normalized = {s.removeprefix("openrouter/") for s in route_models}

    missing = sorted(normalized - catalog)
    assert not missing, f"hardcoded model strings not in OpenRouter catalog: {missing}"


def _dotenv_key():
    from pathlib import Path
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip()
    return None
