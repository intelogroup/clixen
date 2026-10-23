"""Regression guard for the STT backend factory (`tools/stt.py`).

2026-10-23: `set_stt_backend()` built its lookup dict as
`{"parakeet": ParakeetSTT, "phonon2": Phonon2STT}` while the class is spelled
`Phonón2STT` (accented o). The dict literal is evaluated *before* the backend
is looked up, so the `NameError` fired on every call — even for the default
`whisper` backend — and `/voice/model` 500'd, `/voice/process` streamed an
empty 200 with no transcript, and the orb's press-to-talk was dead end to end.
The fallback branch also logged via an undefined `_log` instead of `log`, so
an unimplemented backend would have raised the same way.

These tests keep the factory importable and callable for every backend name,
including the stub ones that must degrade to Whisper.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import stt  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cache():
    """get_stt() caches the backend; isolate each test from the last one."""
    stt._LAST_STT = None
    yield
    stt._LAST_STT = None


def test_default_backend_resolves_to_whisper(monkeypatch):
    monkeypatch.delenv("STT_BACKEND", raising=False)
    backend = stt.get_stt()
    assert backend.name == "whisper"
    assert "ggml-base.bin" in backend.supported_models()


def test_unknown_backend_name_falls_back_to_whisper():
    assert stt.set_stt_backend("no-such-engine").name == "whisper"


@pytest.mark.parametrize("name", ["parakeet", "phonon2"])
def test_unimplemented_stubs_fall_back_without_raising(name):
    # Both stubs raise NotImplementedError in __init__; the factory must log
    # and degrade instead of propagating (this path used to hit `_log`).
    assert stt.set_stt_backend(name).name == "whisper"


def test_every_mapped_class_exists():
    # The exact regression: a dict entry naming a class that does not exist.
    for cls in (stt.WhisperSTT, stt.ParakeetSTT, stt.Phonón2STT):
        assert issubclass(cls, stt.STTBackend)
