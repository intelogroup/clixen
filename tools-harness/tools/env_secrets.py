"""
Secret migration — moves sensitive API keys out of plaintext `.env` into the
macOS Keychain (same backend as tools/vault.py), transparently.

Call load_secrets(KEYS) once at startup, after load_dotenv(). For each key:
  - Keychain has it -> os.environ[key] is set from Keychain (wins over .env).
  - Keychain doesn't have it but .env/.environ does -> migrate: save to
    Keychain, os.environ stays set from the .env value for this run.
  - Neither has it -> left untouched (key stays unset).

Every existing `os.environ.get("OPENROUTER_API_KEY")`-style call site keeps
working unchanged — this only changes where the value originates.
"""

from __future__ import annotations

import logging
import os

from tools.vault import _KEYCHAIN_AVAILABLE, _kc_get, _kc_save

log = logging.getLogger("env_secrets")

_SERVICE_PREFIX = "clixen.secret."

# Keys that get moved out of .env into Keychain. Add new provider keys here.
SENSITIVE_KEYS = (
    "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_REAL_API_KEY",
    "DEEPSEEK_API_KEY", "GROQ_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY",
    "FIRECRAWL_API_KEY", "SERPAPI_API_KEY", "BRAVE_API_KEY", "BRAVE_SEARCH_API_KEY",
    "RAPIDAPI_KEY", "GOOGLE_MAPS_API_KEY", "OLLAMA_API_KEY", "NCBI_API_KEY",
)


def load_secrets(keys: tuple[str, ...] = SENSITIVE_KEYS) -> None:
    if not _KEYCHAIN_AVAILABLE:
        log.warning("Keychain unavailable (non-macOS or no `security` CLI) — secrets stay in .env")
        return
    for key in keys:
        service = _SERVICE_PREFIX + key
        stored = _kc_get(service)
        if stored is not None:
            os.environ[key] = stored.get("value", "")
            continue
        env_val = os.environ.get(key)
        if env_val:
            err = _kc_save(service, {"value": env_val})
            if err:
                log.warning("Could not migrate %s to Keychain: %s", key, err)
            else:
                log.info("Migrated %s from .env to Keychain", key)
