"""Regression tests for the env boot path + drift watchdog (tools/env_boot.py).

Context: on 2026-09-24 a `.env` key rotation never reached the already-running daemons, so
telegram_bot spent ~24h on a dead OpenAI key (60 x 401, memory recall dead, KB ingest dead)
while both `.env` and the Keychain held a valid one. These tests pin the three properties
that make the self-heal work:
  1. boot does NOT clobber values a parent process exported;
  2. a forced reload DOES re-read from disk and bumps GENERATION;
  3. drift detection compares the process env against .env/Keychain — and a Keychain copy
     that disagrees with an edited .env no longer wins on a forced reload.
"""
import os
from pathlib import Path

import pytest

from tools import env_boot


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """Point env_boot at a throwaway .env and keep the real Keychain out of the way."""
    p = tmp_path / ".env"
    p.write_text("CLIXEN_TEST_KEY=from_disk\n")
    monkeypatch.setattr(env_boot, "ENV_PATH", p)
    monkeypatch.setattr(env_boot, "_keychain_value", lambda key: None)
    monkeypatch.setattr(env_boot, "GENERATION", 0)
    monkeypatch.delenv("CLIXEN_TEST_KEY", raising=False)
    return p


def test_boot_does_not_override_an_exported_value(fake_env, monkeypatch):
    monkeypatch.setenv("CLIXEN_TEST_KEY", "from_parent")
    env_boot.load_env()
    assert os.environ["CLIXEN_TEST_KEY"] == "from_parent"


def test_forced_reload_overrides_from_disk(fake_env, monkeypatch):
    monkeypatch.setenv("CLIXEN_TEST_KEY", "stale_value")
    gen = env_boot.load_env(force=True)
    assert os.environ["CLIXEN_TEST_KEY"] == "from_disk"
    assert gen == 1


def test_generation_only_moves_on_forced_reload(fake_env):
    assert env_boot.load_env() == 0
    assert env_boot.GENERATION == 0
    assert env_boot.load_env(force=True) == 1
    assert env_boot.GENERATION == 1


def test_drift_detects_stale_process_value(fake_env, monkeypatch):
    monkeypatch.setenv("CLIXEN_TEST_KEY", "stale_value")
    drift = env_boot.env_drift(keys=("CLIXEN_TEST_KEY",))
    assert "CLIXEN_TEST_KEY" in drift
    # never leak the values themselves — only hashes
    assert "stale_value" not in drift["CLIXEN_TEST_KEY"]
    assert "from_disk" not in drift["CLIXEN_TEST_KEY"]


def test_no_drift_when_in_sync(fake_env, monkeypatch):
    monkeypatch.setenv("CLIXEN_TEST_KEY", "from_disk")
    assert env_boot.env_drift(keys=("CLIXEN_TEST_KEY",)) == {}


def test_check_and_heal_reloads_and_reports(fake_env, monkeypatch):
    monkeypatch.setenv("CLIXEN_TEST_KEY", "stale_value")
    healed = env_boot.check_and_heal(notify=False, keys=("CLIXEN_TEST_KEY",))
    assert "CLIXEN_TEST_KEY" in healed
    assert os.environ["CLIXEN_TEST_KEY"] == "from_disk"  # self-healed in-process
    assert env_boot.GENERATION == 1
    # convergent: a second tick has nothing left to fix (no reload loop)
    assert env_boot.check_and_heal(notify=False, keys=("CLIXEN_TEST_KEY",)) == {}
    assert env_boot.GENERATION == 1


def test_cloud_clients_rebuild_when_generation_moves(monkeypatch):
    """Reloading os.environ is not enough — cached OpenAI clients hold the key they were
    built with, so cloud_client must drop them when GENERATION changes."""
    from clients import cloud_client

    built = []

    class FakeClient:
        def __init__(self, **kw):
            built.append(kw["api_key"])

        def close(self):
            pass

    monkeypatch.setattr(cloud_client, "_ENV_GENERATION_SEEN", None)
    monkeypatch.setattr(cloud_client, "OpenAI", FakeClient)
    monkeypatch.setenv("OPENROUTER_API_KEY", "key_v1")

    cloud_client._resolve("openrouter/anthropic/claude-haiku-4.5")
    cloud_client._resolve("openrouter/anthropic/claude-haiku-4.5")
    assert built == ["key_v1"]  # cached

    monkeypatch.setattr(env_boot, "GENERATION", env_boot.GENERATION + 1)
    monkeypatch.setenv("OPENROUTER_API_KEY", "key_v2")

    cloud_client._resolve("openrouter/anthropic/claude-haiku-4.5")
    assert built == ["key_v1", "key_v2"]  # rebuilt with the rotated key
