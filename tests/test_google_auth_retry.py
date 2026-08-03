import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools-harness"))

from tools import google_auth


class _Resp:
    def __init__(self, status):
        self.status = status


class _GoogleError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.resp = _Resp(status)


def test_execute_with_google_auth_retry_refreshes_once(monkeypatch):
    calls = {"api": 0, "refresh": 0}

    def fake_refresh():
        calls["refresh"] += 1
        return "Token refreshed successfully. Expires: soon. Scopes: 4 active."

    def flaky_call():
        calls["api"] += 1
        if calls["api"] == 1:
            raise _GoogleError(401, "Invalid Credentials")
        return {"ok": True}

    monkeypatch.setattr(google_auth, "refresh_google_token", fake_refresh)

    assert google_auth.execute_with_google_auth_retry(flaky_call) == {"ok": True}
    assert calls == {"api": 2, "refresh": 1}


def test_execute_with_google_auth_retry_does_not_refresh_missing_scopes(monkeypatch):
    calls = {"refresh": 0}

    def fake_refresh():
        calls["refresh"] += 1
        return "Token refreshed successfully."

    def missing_scope_call():
        raise _GoogleError(403, "Request had insufficient authentication scopes.")

    monkeypatch.setattr(google_auth, "refresh_google_token", fake_refresh)

    try:
        google_auth.execute_with_google_auth_retry(missing_scope_call)
    except _GoogleError:
        pass
    else:
        raise AssertionError("missing-scope errors should be raised")

    assert calls["refresh"] == 0
