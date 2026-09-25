"""Spill files must never persist secrets: oversized tool output is written to
disk AND previewed in-band, and the tool result may contain a credential
(vault value, API key, JWT). Redaction happens before either surface.
"""
import re
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import spill


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(spill, "_SPILL_DIR", tmp_path / ".spill")
    monkeypatch.setattr(spill, "_vault_secret_values", lambda: frozenset())


def _big(marker: str) -> str:
    return marker + " " + ("x" * 6000)


def test_vault_secret_never_reaches_disk_or_preview(monkeypatch):
    secret = "VAULT-PASSWORD-abc123"
    monkeypatch.setattr(spill, "_vault_secret_values", lambda: frozenset({secret}))
    out = spill.spill(_big(secret), "browser_open")
    assert secret not in out, "preview leaked the secret"
    files = list((spill._SPILL_DIR).iterdir())
    assert len(files) == 1
    assert secret not in files[0].read_text(), "spill file leaked the secret"
    assert "[REDACTED" in files[0].read_text()


def test_credential_shapes_are_masked():
    for token in ("sk-abcdefghijklmnop1234", "ghp_abcdefghijklmnopqrstuvwxyz0123",
                  "AKIAIOSFODNN7EXAMPLE",
                  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"):
        out = spill.spill(_big(token), "browser_open")
        assert token not in out, f"{token[:8]}… leaked in preview"
        assert "[REDACTED" in out


def test_keyed_secret_in_free_text_is_masked():
    out = spill.spill(_big("password = hunter2xyz"), "browser_open")
    assert "hunter2xyz" not in out


def test_spill_file_is_owner_only():
    spill.spill(_big("harmless"), "read_file")
    path = next(iter(spill._SPILL_DIR.iterdir()))
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_locator_flags_redaction_only_when_it_happened(monkeypatch):
    clean = spill.spill(_big("harmless"), "read_file")
    assert "redacted" not in clean.lower()
    monkeypatch.setattr(spill, "_vault_secret_values", lambda: frozenset({"VAULT-PW-abc123"}))
    dirty = spill.spill(_big("VAULT-PW-abc123"), "read_file")
    assert "redacted" in dirty.lower()


def test_small_results_pass_through_untouched():
    assert spill.spill("short", "read_file") == "short"
    assert not spill._SPILL_DIR.exists()


def test_oversized_but_clean_output_is_preserved():
    body = "y" * 5000
    out = spill.spill(body, "read_file")
    path = Path(re.search(r"saved to (\S+) —", out).group(1))
    assert path.read_text() == body
