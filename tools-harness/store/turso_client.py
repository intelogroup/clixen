"""
Turso (libSQL) edge — Hrana-over-HTTP, zero new dependencies.

Why this exists (2026-10-24): science scout's claim ledger is local SQLite only.
A `science-scout` Turso database was provisioned so the same ledger is reachable
off-machine. Instead of pulling in `libsql-client` (native-build/transport
churn), this speaks the documented `/v2/pipeline` JSON endpoint with `requests`
— one POST can carry N statements, which is also what makes the bulk migration
and its retry-free batching cheap.

Design rules, because every caller sits on a hot write path:
  * `mirror()` NEVER raises — a Turso outage must not break a local write. The
    local SQLite write is the source of truth; the mirror is a replica.
  * Circuit breaker: `TURSO_FAIL_THRESHOLD` consecutive failures open the
    circuit for `TURSO_COOLDOWN_S` seconds, so an offline laptop pays a couple
    of socket timeouts instead of one per store call for the whole scan.
  * Credentials resolve lazily (`.env` then Keychain). Daemons load `.env` once
    at import time, so a module imported before that would otherwise see empty
    variables forever — the same footgun that made `google_auth.py` load dotenv
    at import.

Env:
  TURSO_DATABASE_URL   libsql://<db>-<org>.<region>.turso.io  (not a secret)
  TURSO_AUTH_TOKEN     DB token; Keychain service clixen.secret.TURSO_AUTH_TOKEN
  TURSO_DISABLE=1      hard-off the mirror (local-only mode)
  TURSO_TIMEOUT        seconds per request (default 10)
  TURSO_FAIL_THRESHOLD consecutive failures before the breaker opens (default 3)
  TURSO_COOLDOWN_S     seconds the breaker stays open (default 120)
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger("turso_client")

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"  # tools-harness/.env

_FAIL_THRESHOLD = max(1, int(os.environ.get("TURSO_FAIL_THRESHOLD", "3") or 3))
_COOLDOWN_S = float(os.environ.get("TURSO_COOLDOWN_S", "120") or 120)
_TIMEOUT_S = float(os.environ.get("TURSO_TIMEOUT", "10") or 10)

_lock = threading.Lock()          # guards creds + breaker counters
_http_lock = threading.Lock()     # requests.Session is not thread-safe
_session: requests.Session | None = None
_failures = 0
_open_until = 0.0
_last_error = ""
_creds_loaded = False


class TursoError(RuntimeError):
    """Raised by execute()/query()/execute_batch(). mirror() never raises it."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


# ── credentials ────────────────────────────────────────────────────────────


def _read_env_file() -> None:
    """Load tools-harness/.env for any *missing* TURSO_* var only."""
    if not _ENV_FILE.is_file():
        return
    try:
        from dotenv import dotenv_values

        for key, value in dotenv_values(_ENV_FILE).items():
            if key.startswith("TURSO_") and value and not os.environ.get(key):
                os.environ[key] = value
    except Exception as exc:  # noqa: BLE001 — config best-effort
        log.debug("turso: could not read %s: %s", _ENV_FILE, exc)


def _ensure_creds() -> None:
    global _creds_loaded
    if _creds_loaded:
        return
    with _lock:
        if _creds_loaded:
            return
        if not (os.environ.get("TURSO_DATABASE_URL") and os.environ.get("TURSO_AUTH_TOKEN")):
            _read_env_file()
            if not os.environ.get("TURSO_AUTH_TOKEN"):
                try:
                    from tools.env_secrets import load_secrets

                    load_secrets(("TURSO_AUTH_TOKEN",))
                except Exception as exc:  # noqa: BLE001 — non-macOS / no keychain
                    log.debug("turso: keychain lookup failed: %s", exc)
        _creds_loaded = True


def _db_url() -> str:
    return (os.environ.get("TURSO_DATABASE_URL") or "").strip()


def _token() -> str:
    return (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()


def enabled() -> bool:
    """True when the mirror should be attempted at all."""
    if os.environ.get("TURSO_DISABLE", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    _ensure_creds()
    return bool(_db_url() and _token())


def disabled_reason() -> str:
    if os.environ.get("TURSO_DISABLE", "").strip().lower() in ("1", "true", "yes", "on"):
        return "TURSO_DISABLE is set"
    _ensure_creds()
    if not _db_url():
        return "TURSO_DATABASE_URL is not set"
    if not _token():
        return "TURSO_AUTH_TOKEN is not set"
    return ""


# ── circuit breaker ────────────────────────────────────────────────────────


def _circuit_open() -> bool:
    with _lock:
        return time.monotonic() < _open_until


def _record_ok() -> None:
    global _failures, _open_until, _last_error
    with _lock:
        _failures = 0
        _open_until = 0.0
        _last_error = ""


def _record_fail(exc: BaseException) -> None:
    global _failures, _open_until, _last_error
    with _lock:
        _failures += 1
        _last_error = f"{type(exc).__name__}: {exc}"
        if _failures >= _FAIL_THRESHOLD:
            _open_until = time.monotonic() + _COOLDOWN_S
            log.warning(
                "turso: %d consecutive failures — mirror paused %.0fs (%s)",
                _failures, _COOLDOWN_S, _last_error,
            )


def reset_breaker() -> None:
    """Test/debug hook — clears failure counters and reopens the circuit."""
    _record_ok()


def stats() -> dict:
    with _lock:
        return {
            "enabled": enabled(),
            "disabled_reason": disabled_reason(),
            "failures": _failures,
            "circuit_open": time.monotonic() < _open_until,
            "cooldown_remaining_s": max(0.0, round(_open_until - time.monotonic(), 1)),
            "last_error": _last_error,
        }


# ── transport ──────────────────────────────────────────────────────────────


def _http_url() -> str:
    raw = _db_url()
    if not raw:
        raise TursoError("TURSO_DATABASE_URL is not set")
    if raw.startswith("libsql://"):
        raw = "https://" + raw[len("libsql://"):]
    elif raw.startswith("wss://"):
        raw = "https://" + raw[len("wss://"):]
    elif raw.startswith("ws://"):
        raw = "http://" + raw[len("ws://"):]
    raw = raw.rstrip("/")
    if not raw.endswith("/v2/pipeline"):
        raw += "/v2/pipeline"
    return raw


def _get_session() -> requests.Session:
    global _session
    with _lock:
        if _session is None:
            _session = requests.Session()
        return _session


def _encode(value) -> dict:
    """Python value -> Hrana typed value.

    Hrana's wire format is asymmetric: `integer` carries the i64 as a *string*
    (JSON numbers lose 64-bit precision), but `float` must be a real JSON
    number (f64) — sending it as a string is rejected with
    `invalid type: string ..., expected f64`. Learned the hard way exporting
    confirmations.db's REAL timestamp column.
    """
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN/inf: JSON has no literal
            return {"type": "null"}
        return {"type": "float", "value": value}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode()}
    return {"type": "text", "value": str(value)}


def _decode(cell: dict):
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell.get("value") or 0)
    if kind == "float":
        return float(cell.get("value") or 0.0)
    if kind == "blob":
        return base64.b64decode(cell.get("base64") or "")
    return cell.get("value")


def _stmt(sql: str, params: tuple | list) -> dict:
    stmt: dict = {"sql": sql}
    if params:
        stmt["args"] = [_encode(p) for p in params]
    return {"type": "execute", "stmt": stmt}


def _post(statements: list[tuple[str, tuple | list]], timeout: float | None = None) -> list[dict]:
    """One HTTP round trip carrying every statement. Raises TursoError."""
    if not enabled():
        raise TursoError(f"Turso disabled: {disabled_reason()}")
    payload = {"requests": [_stmt(sql, params) for sql, params in statements] + [{"type": "close"}]}
    headers = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}
    try:
        with _http_lock:
            resp = _get_session().post(
                _http_url(), json=payload, headers=headers,
                timeout=timeout or _TIMEOUT_S,
            )
    except Exception as exc:  # noqa: BLE001 — network layer
        raise TursoError(f"transport error: {type(exc).__name__}: {exc}") from exc

    if resp.status_code >= 400:
        raise TursoError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise TursoError(f"non-JSON response: {resp.text[:200]}") from exc

    parsed: list[dict] = []
    for item in body.get("results") or []:
        if item.get("type") == "error":
            err = item.get("error") or {}
            raise TursoError(err.get("message") or "unknown Turso error", err.get("code") or "")
        response = item.get("response") or {}
        if response.get("type") != "execute":
            continue  # the trailing 'close' ack
        result = response.get("result") or {}
        cols = [c.get("name") for c in result.get("cols") or []]
        rows = [
            {cols[i]: _decode(cell) for i, cell in enumerate(row) if i < len(cols)}
            for row in result.get("rows") or []
        ]
        parsed.append({
            "rows": rows,
            "affected": int(result.get("affected_row_count") or 0),
            "last_insert_rowid": result.get("last_insert_rowid"),
        })
    return parsed


# ── public API ─────────────────────────────────────────────────────────────


def execute(sql: str, params: tuple | list = (), timeout: float | None = None) -> dict:
    """Run one statement. Raises TursoError on any failure."""
    if _circuit_open():
        raise TursoError("circuit open — mirror paused after repeated failures")
    try:
        result = _post([(sql, params)], timeout=timeout)[0]
    except TursoError as exc:
        _record_fail(exc)
        raise
    _record_ok()
    return result


def query(sql: str, params: tuple | list = (), timeout: float | None = None) -> list[dict]:
    """Run one SELECT and return row dicts."""
    return execute(sql, params, timeout=timeout)["rows"]


def execute_batch(
    statements: list[tuple[str, tuple | list]], timeout: float | None = None,
) -> list[dict]:
    """Run many statements in a single round trip (used by the migration)."""
    if not statements:
        return []
    if _circuit_open():
        raise TursoError("circuit open — mirror paused after repeated failures")
    try:
        results = _post(statements, timeout=timeout)
    except TursoError as exc:
        _record_fail(exc)
        raise
    _record_ok()
    return results


def mirror(sql: str, params: tuple | list = ()) -> bool:
    """Best-effort replica write. Returns False (never raises) on any problem.

    Callers use this *after* their local SQLite commit — losing a mirror write
    degrades the replica, it never corrupts the source of truth.
    """
    try:
        if not enabled() or _circuit_open():
            return False
        execute(sql, params)
        return True
    except Exception as exc:  # noqa: BLE001 — the whole point of this function
        log.warning("turso mirror failed: %s", exc)
        return False


def mirror_batch(statements: list[tuple[str, tuple | list]]) -> bool:
    """Best-effort replica write of N statements in one round trip.

    Used by store/dbclose.MirroredConnection, which buffers a transaction's
    writes and spills them on commit. Same contract as mirror(): never raises.
    """
    try:
        if not statements or not enabled() or _circuit_open():
            return False
        execute_batch(statements)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("turso mirror batch failed (%d stmts): %s", len(statements), exc)
        return False


def ping() -> str:
    """Human-readable connectivity check (CLI / smoke tests)."""
    if not enabled():
        return f"disabled: {disabled_reason()}"
    rows = query("select sqlite_version() as v, (select count(*) from sqlite_master) as objs")
    row = rows[0] if rows else {}
    return f"ok sqlite={row.get('v')} schema_objects={row.get('objs')} url={_db_url()}"


