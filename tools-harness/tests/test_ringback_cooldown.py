"""Cooldown gate on tools.connector_ringback.call_my_phone — a burst of qualifying
findings (e.g. several science_scout STRONG_EVIDENCE papers in one run) must not
ring back-to-back."""
import importlib
import os

import tools.connector_ringback as cr


def test_strip_markdown_removes_tts_unfriendly_symbols():
    assert cr._strip_markdown("**bold** and _italic_") == "bold and italic"
    assert cr._strip_markdown("# Heading\n- bullet one\n- bullet two") == " Heading\nbullet one\nbullet two"
    assert cr._strip_markdown("inline `code` here") == "inline code here"
    assert cr._strip_markdown("plain text, no markdown") == "plain text, no markdown"


def test_second_call_within_window_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("RINGBACK_CALL_COOLDOWN_SECONDS", "900")
    import tools.connector_ringback as cr
    importlib.reload(cr)
    monkeypatch.setattr(cr, "_COOLDOWN_FILE", tmp_path / ".last_call_ts")

    allowed1, _, _ = cr._check_and_update_cooldown()
    allowed2, elapsed2, _ = cr._check_and_update_cooldown()

    assert allowed1 is True
    assert allowed2 is False
    assert elapsed2 < 900


def test_call_allowed_again_after_window_elapses(tmp_path, monkeypatch):
    monkeypatch.setenv("RINGBACK_CALL_COOLDOWN_SECONDS", "900")
    import tools.connector_ringback as cr
    importlib.reload(cr)
    monkeypatch.setattr(cr, "_COOLDOWN_FILE", tmp_path / ".last_call_ts")

    cr._check_and_update_cooldown()
    (tmp_path / ".last_call_ts").write_text(str(__import__("time").time() - 901))

    allowed, _, _ = cr._check_and_update_cooldown()
    assert allowed is True


def test_failed_call_restores_previous_timestamp(tmp_path, monkeypatch):
    """A docker/network failure must not burn the cooldown window on nothing —
    the next genuine call attempt should still be allowed right away."""
    monkeypatch.setenv("RINGBACK_CALL_COOLDOWN_SECONDS", "900")
    import tools.connector_ringback as cr
    importlib.reload(cr)
    monkeypatch.setattr(cr, "_COOLDOWN_FILE", tmp_path / ".last_call_ts")

    allowed1, _, previous_ts = cr._check_and_update_cooldown()
    assert allowed1 is True
    cr._restore_cooldown(previous_ts)

    allowed2, _, _ = cr._check_and_update_cooldown()
    assert allowed2 is True


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
