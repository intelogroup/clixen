"""Single env boot path + drift watchdog for every long-running entrypoint.

Why this exists (2026-09-24): each process loads `.env` + the Keychain ONCE at import
(`load_dotenv` in core.py / telegram_bot.py / jobs/worker.py, `load_secrets()` in core.py and
harness.py). When a key is rotated on disk, an already-running daemon keeps the old value
in-process forever. Live fallout that day: `.env` was rotated at 09:59 while telegram_bot had
booted at 08:51, so for ~24h every OpenAI call in that process 401'd on a key ending `tX8A`
while both `.env` and the Keychain held a valid key ending `UocA` —
  * the router classifier fell back to OpenRouter on every single message (60 logged 401s),
  * `memory_tools.recall_block()` returned "" for every turn (40 `recall skipped: 401`),
  * voice-note KB ingestion failed every time (6 `KB store failed: 401`).
A restart fixed all three. This module exists so a restart is no longer required.

GENERATION is bumped whenever keys are re-read, and clients/cloud_client.py drops its cached
OpenAI clients when it sees a new generation — reloading os.environ alone does nothing,
because those clients captured the old key at construction time.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

log = logging.getLogger("env_boot")

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# Bumped on every forced (post-boot) reload. Consumers that cache per-key objects — see
# cloud_client._resolve — compare the value they saw last and rebuild when it changes.
GENERATION: int = 0

_watchdog_thread: threading.Thread | None = None


def load_env(force: bool = False) -> int:
    """Load `.env` then the Keychain, in the same order every entrypoint uses.

    force=False is the boot path: existing os.environ values win (python-dotenv's own
    default), so a parent process that already exported a key keeps it.
    force=True re-reads from disk and OVERRIDES the process env — the self-heal path used
    when drift is detected after boot. Returns the (possibly bumped) GENERATION.
    """
    global GENERATION

    load_dotenv(ENV_PATH, override=force)

    try:
        from tools.env_secrets import load_secrets

        load_secrets(prefer_env=force)
    except Exception as e:  # Keychain unavailable / non-macOS — .env alone is fine
        log.debug("load_secrets skipped: %s", e)

    if force:
        GENERATION += 1
        log.warning("[env] keys re-read from %s + Keychain (generation %d)", ENV_PATH, GENERATION)
    return GENERATION


def _dotenv_values() -> dict[str, str]:
    try:
        return {k: (v or "") for k, v in dotenv_values(ENV_PATH).items()}
    except Exception as e:
        log.debug("could not read %s: %s", ENV_PATH, e)
        return {}


def _keychain_value(key: str) -> str | None:
    try:
        from tools.vault import _KEYCHAIN_AVAILABLE, _kc_get

        if not _KEYCHAIN_AVAILABLE:
            return None
        stored = _kc_get("clixen.secret." + key)
        return (stored or {}).get("value")
    except Exception:
        return None


def env_drift(keys: tuple[str, ...] | None = None) -> dict[str, str]:
    """Keys whose value in THIS process differs from what's on disk.

    Returns {KEY: "<source>=<sha8> vs env=<sha8>"} — never the values themselves, so the
    result is safe to log or send over Telegram.
    """
    import hashlib

    if keys is None:
        from tools.env_secrets import SENSITIVE_KEYS as keys  # noqa: F811

    def sha8(v: str) -> str:
        return hashlib.sha256(v.encode()).hexdigest()[:8]

    disk = _dotenv_values()
    drift: dict[str, str] = {}
    for key in keys:
        env_val = os.environ.get(key) or ""
        dot_val = disk.get(key) or ""
        kc_val = _keychain_value(key)
        others = [v for v in (dot_val, kc_val) if v]
        if not others:
            continue  # absent everywhere — doctor.py's job, not drift's
        if env_val and all(env_val == v for v in others):
            continue
        if not env_val and len(set(others)) == 1:
            continue  # never booted with it in this process; nothing stale here
        detail = " ".join(
            f"{src}={sha8(v)}" for src, v in ((".env", dot_val), ("keychain", kc_val or "")) if v
        )
        drift[key] = f"{detail} vs env={sha8(env_val) if env_val else 'unset'}"
    return drift


def check_and_heal(notify: bool = True, keys: tuple[str, ...] | None = None) -> dict[str, str]:
    """One watchdog tick: detect drift, reload, optionally alert. Returns what drifted.

    keys=None checks env_secrets.SENSITIVE_KEYS (the real watchdog's scope); pass an explicit
    tuple to scope it. Split out of the thread loop so it can be tested deterministically.
    """
    drift = env_drift(keys)
    if not drift:
        return {}

    log.warning(
        "[env] stale in-process keys detected — reloading: %s",
        "; ".join(f"{k} ({v})" for k, v in drift.items()),
    )
    load_env(force=True)

    if notify:
        try:
            from tools.telegram_send import send_telegram

            send_telegram(
                "⚠️ clixen: stale env in a running process — reloaded keys from disk.\n"
                + "\n".join(f"• {k}: {v}" for k, v in drift.items())
            )
        except Exception as e:
            log.debug("drift alert not sent: %s", e)
    return drift


def start_env_watchdog(interval_s: float = 300.0) -> threading.Thread | None:
    """Self-heal env drift in a long-running process (idempotent, daemon thread)."""
    global _watchdog_thread
    if _watchdog_thread is not None and _watchdog_thread.is_alive():
        return _watchdog_thread

    def _loop() -> None:
        import time

        while True:
            time.sleep(interval_s)
            try:
                check_and_heal()
            except Exception:
                log.exception("[env] watchdog tick failed")

    _watchdog_thread = threading.Thread(target=_loop, name="env-watchdog", daemon=True)
    _watchdog_thread.start()
    log.info("[env] drift watchdog started (every %.0fs)", interval_s)
    return _watchdog_thread
