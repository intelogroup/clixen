import time

from tools import rate_limit


def test_reserve_then_refuse_within_window(tmp_path, monkeypatch):
    monkeypatch.setattr(rate_limit, "_STATE_DIR", tmp_path)
    allowed1, _, _ = rate_limit.check_and_reserve("k", cooldown_seconds=900)
    allowed2, elapsed2, _ = rate_limit.check_and_reserve("k", cooldown_seconds=900)
    assert allowed1 is True
    assert allowed2 is False
    assert elapsed2 < 900


def test_allowed_again_after_window_elapses(tmp_path, monkeypatch):
    monkeypatch.setattr(rate_limit, "_STATE_DIR", tmp_path)
    rate_limit.check_and_reserve("k", cooldown_seconds=900)
    rate_limit._state_file("k").write_text(str(time.time() - 901))
    allowed, _, _ = rate_limit.check_and_reserve("k", cooldown_seconds=900)
    assert allowed is True


def test_restore_undoes_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(rate_limit, "_STATE_DIR", tmp_path)
    allowed1, _, previous_ts = rate_limit.check_and_reserve("k", cooldown_seconds=900)
    assert allowed1 is True
    rate_limit.restore("k", previous_ts)
    allowed2, _, _ = rate_limit.check_and_reserve("k", cooldown_seconds=900)
    assert allowed2 is True


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
