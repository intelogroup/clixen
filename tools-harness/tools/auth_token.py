"""
Localhost auth token — E3 hardening.

Binding to 127.0.0.1 (already done in chat_ui.py/core.py) stops LAN/remote
access, but NOT other local processes: any other app or script on the same
machine can still `curl localhost:9234/chat` and drive every tool. This
issues one random token per install, stored in Keychain (same backend as
tools/vault.py), and chat_ui.py's middleware requires it on every request
except the first HTML page load (which gets it via a Set-Cookie so the
browser UI keeps working with zero client-side change).
"""

from __future__ import annotations

import logging
import secrets

from tools.vault import _KEYCHAIN_AVAILABLE, _kc_get, _kc_save

log = logging.getLogger("auth_token")

_SERVICE = "clixen.localhost.token"


def get_or_create_token() -> str | None:
    """Returns a stable per-install token, or None if Keychain unavailable
    (non-macOS dev boxes fall back to no auth gate — same as today)."""
    if not _KEYCHAIN_AVAILABLE:
        log.warning("Keychain unavailable — localhost auth token disabled, falling back to bind-only protection")
        return None
    stored = _kc_get(_SERVICE)
    if stored is not None:
        return stored.get("value")
    token = secrets.token_urlsafe(32)
    err = _kc_save(_SERVICE, {"value": token})
    if err:
        log.warning("Could not persist localhost auth token: %s", err)
        return None
    return token
