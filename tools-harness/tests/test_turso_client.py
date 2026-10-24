"""Turso edge unit tests — no network, no credentials.

What would silently corrupt the replica if it regressed:
  * typed value encoding (an int sent as text changes ordering/constraints)
  * URL normalization (`libsql://` must become `https://…/v2/pipeline`)
  * the mirror contract: never raise, and stop hammering a dead endpoint
    (circuit breaker) instead of paying a timeout on every store write.
"""
from __future__ import annotations

import json

import pytest

from store import turso_client


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """Records every POST; returns queued payloads in order."""

    def __init__(self, payloads=None, exc: Exception | None = None):
        self.payloads = list(payloads or [])
        self.exc = exc
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002 — mirrors requests
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.exc:
            raise self.exc
        payload = self.payloads.pop(0) if self.payloads else {"results": []}
        return FakeResponse(payload)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://science-scout-intelogroup.aws-us-east-2.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("TURSO_DISABLE", raising=False)
    monkeypatch.setattr(turso_client, "_creds_loaded", True)
    turso_client.reset_breaker()
    yield monkeypatch
    turso_client.reset_breaker()
    turso_client._session = None


@pytest.fixture
def fake_session(configured, monkeypatch):
    def _install(session: FakeSession) -> FakeSession:
        monkeypatch.setattr(turso_client, "_session", session)
        return session

    return _install


def _ok_result(rows=None, affected=0):
    return {
        "results": [
            {"type": "ok", "response": {"type": "execute", "result": {
                "cols": [], "rows": rows or [], "affected_row_count": affected,
                "last_insert_rowid": None,
            }}},
            {"type": "ok", "response": {"type": "close"}},
        ]
    }


def test_encode_typed_values():
    assert turso_client._encode(None) == {"type": "null"}
    assert turso_client._encode(True) == {"type": "integer", "value": "1"}
    assert turso_client._encode(7) == {"type": "integer", "value": "7"}
    # floats must be JSON numbers (f64); integers must be strings (i64 precision)
    assert turso_client._encode(1.5) == {"type": "float", "value": 1.5}
    assert turso_client._encode("hi") == {"type": "text", "value": "hi"}
    assert turso_client._encode(b"\x00\xff") == {"type": "blob", "base64": "AP8="}


def test_encode_non_finite_floats_become_null():
    """JSON has no NaN/Infinity literal — Hrana would 400 the whole batch."""
    assert turso_client._encode(float("nan")) == {"type": "null"}
    assert turso_client._encode(float("inf")) == {"type": "null"}
    assert turso_client._encode(float("-inf")) == {"type": "null"}


def test_decode_round_trip():
    assert turso_client._decode({"type": "null"}) is None
    assert turso_client._decode({"type": "integer", "value": "42"}) == 42
    assert turso_client._decode({"type": "float", "value": "2.25"}) == 2.25
    assert turso_client._decode({"type": "text", "value": "x"}) == "x"
    assert turso_client._decode({"type": "blob", "base64": "AP8="}) == b"\x00\xff"


def test_http_url_normalizes_libsql_scheme(configured):
    assert turso_client._http_url() == (
        "https://science-scout-intelogroup.aws-us-east-2.turso.io/v2/pipeline"
    )
    configured.setenv("TURSO_DATABASE_URL", "https://example.turso.io")
    assert turso_client._http_url() == "https://example.turso.io/v2/pipeline"
    configured.setenv("TURSO_DATABASE_URL", "https://example.turso.io/v2/pipeline")
    assert turso_client._http_url().count("/v2/pipeline") == 1


def test_mirror_sends_typed_args(configured, fake_session):
    session = fake_session(FakeSession([_ok_result(affected=1)]))
    assert turso_client.mirror("UPDATE t SET a = ? WHERE b = ?", ("str", 5)) is True
    sent = session.calls[0]["json"]
    stmt = sent["requests"][0]["stmt"]
    assert stmt["sql"] == "UPDATE t SET a = ? WHERE b = ?"
    assert stmt["args"] == [{"type": "text", "value": "str"}, {"type": "integer", "value": "5"}]
    assert sent["requests"][-1] == {"type": "close"}
    assert session.calls[0]["headers"]["Authorization"] == "Bearer test-token"
    assert session.calls[0]["url"].startswith("https://")


def test_mirror_never_raises_and_opens_breaker(configured, fake_session):
    fake_session(FakeSession(exc=OSError("network down")))
    for _ in range(turso_client._FAIL_THRESHOLD):
        assert turso_client.mirror("INSERT INTO t (a) VALUES (?)", ("x",)) is False
    assert turso_client.stats()["circuit_open"] is True
    session = turso_client._session
    before = len(session.calls)
    assert turso_client.mirror("INSERT INTO t (a) VALUES (?)", ("y",)) is False
    assert len(session.calls) == before, "open circuit must not hit the network again"


def test_mirror_noop_when_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("TURSO_DISABLE", raising=False)  # conftest sets it suite-wide
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(turso_client, "_creds_loaded", True)
    monkeypatch.setattr(turso_client, "_ENV_FILE", tmp_path / "absent.env")
    assert turso_client.enabled() is False
    assert "TURSO_DATABASE_URL" in turso_client.disabled_reason()
    assert turso_client.mirror("INSERT INTO t (a) VALUES (?)", ("x",)) is False


def test_execute_raises_on_error_result(configured, fake_session):
    fake_session(FakeSession([{"results": [
        {"type": "error", "error": {"message": "no such table: t", "code": "SQLITE_UNKNOWN"}},
    ]}]))
    with pytest.raises(turso_client.TursoError) as exc:
        turso_client.execute("SELECT * FROM t")
    assert exc.value.code == "SQLITE_UNKNOWN"
    assert "no such table" in str(exc.value)


def test_execute_raises_on_http_error(configured, fake_session):
    session = FakeSession()
    fake_session(session)
    session.post = lambda *a, **kw: FakeResponse({"error": "unauthorized"}, status_code=401)
    with pytest.raises(turso_client.TursoError, match="HTTP 401"):
        turso_client.execute("SELECT 1")

