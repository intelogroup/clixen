"""Search agent configuration loader.

Loads unified config from search_config.yaml.
Use this instead of hardcoded constants.
"""

import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

# Find config file relative to this module
_CONFIG_DIR = Path(__file__).parent
_CONFIG_PATH = _CONFIG_DIR / "search_config.yaml"

# Cache loaded config
_config: dict[str, Any] | None = None


def _load_config() -> dict[str, Any]:
    """Load and cache YAML config."""
    global _config
    if _config is None:
        with open(_CONFIG_PATH, "r") as f:
            _config = yaml.safe_load(f)
    return _config


def get_junk_domains() -> frozenset[str]:
    """Return junk domains to filter from search results."""
    return frozenset(_load_config().get("junk_domains", []))


def get_source_authority() -> dict[str, float]:
    """Return source authority scores for merge ranking."""
    return _load_config().get("source_authority", {"ddg": 1.0})


def get_timeouts() -> dict[str, float]:
    """Return timeout values in seconds."""
    return _load_config().get("timeouts", {"ddg": 4.0})


def get_enrich_config() -> dict[str, Any]:
    """Return enrichment thresholds."""
    return _load_config().get("enrich", {})


def get_domains() -> list[str]:
    """Return allowed classification domains."""
    return _load_config().get("domains", ["general"])


def get_intents() -> list[str]:
    """Return allowed classification intents."""
    return _load_config().get("intents", ["recent"])


def get_gov_triggers() -> dict[str, dict[str, Any]]:
    """Return GovAdapter triggers by category."""
    return _load_config().get("gov_triggers", {})


def get_all_gov_keywords() -> list[str]:
    """Return all gov trigger keywords as flat list."""
    triggers = get_gov_triggers()
    return triggers.get("all_keywords", [])


def get_domain_keywords() -> dict[str, list[str]]:
    """Return domain keyword triggers for adapter routing."""
    return _load_config().get("domain_keywords", {})


def get_semiconductor_companies() -> dict[str, dict[str, Any]]:
    """Return semiconductor company config for adapter."""
    return _load_config().get("semiconductor_companies", {})


def get_gov_trigger_keywords() -> frozenset[str]:
    """Return GovAdapter trigger keywords as frozenset."""
    return frozenset(get_all_gov_keywords())


def is_junk_domain(url: str) -> bool:
    """Check if URL's domain is in the junk blocklist."""
    from urllib.parse import urlparse

    try:
        netloc = urlparse(url).netloc.lower()
        return any(bad in netloc for bad in get_junk_domains())
    except Exception:
        log.debug("URL parse failed in is_junk_url for %s", url, exc_info=True)
        return False


# Backwards compatibility - expose the config for direct access
CONFIG = _load_config()
