"""Cooldown gate on call_my_phone — a burst of qualifying findings (e.g. several
science_scout STRONG_EVIDENCE papers in one run) must not ring back-to-back.

The cooldown itself now lives in tools/rate_limit.py, enforced centrally by
tools/registry.py::_execute_raw (see _RATE_LIMITS) instead of inside
connector_ringback.py — see test_rate_limit.py for the generic mechanism and
test_execute_raw_rate_limit below for the registry wiring."""
import tools.connector_ringback as cr
from tools import rate_limit


def test_strip_markdown_removes_tts_unfriendly_symbols():
    assert cr._strip_markdown("**bold** and _italic_") == "bold and italic"
    assert cr._strip_markdown("# Heading\n- bullet one\n- bullet two") == " Heading\nbullet one\nbullet two"
    assert cr._strip_markdown("inline `code` here") == "inline code here"
    assert cr._strip_markdown("plain text, no markdown") == "plain text, no markdown"


def test_call_my_phone_builds_docker_env_without_crashing(monkeypatch):
    """Regression: _run_call's env-building line (`dict(os.environ)`) once crashed
    every real call with NameError because `import os` had been dropped from the
    module during a refactor — the bug was invisible until a live call ran, since
    no test exercised past that line. This drives call_my_phone up through env
    construction with a stub stdio_client that raises a sentinel right after, so
    a missing `os` import fails this test instead of only failing on a real call."""
    import mcp.client.stdio as stdio_mod

    class _Boom(Exception):
        pass

    class _FakeStdioClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise _Boom("sentinel: reached stdio_client, env build did not crash")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(stdio_mod, "stdio_client", _FakeStdioClient)

    result = cr.call_my_phone("test message")

    assert "not defined" not in result
    assert "NameError" not in result
    assert "sentinel: reached stdio_client" in result


def test_execute_raw_rate_limit(monkeypatch, tmp_path):
    import tools.registry as registry

    monkeypatch.setattr(rate_limit, "_STATE_DIR", tmp_path)
    monkeypatch.setitem(registry.EXECUTORS, "call_my_phone", lambda args: "[ringback ok] called")

    first = registry._execute_raw("call_my_phone", {"message": "hi"})
    second = registry._execute_raw("call_my_phone", {"message": "hi again"})

    assert first == "[ringback ok] called"
    assert second.startswith("[ringback cooldown]")
    assert "callable again in" in second


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
