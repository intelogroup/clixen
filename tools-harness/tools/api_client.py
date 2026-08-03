"""
API-first data extraction agent — token management + httpx calls + auto-refresh.

Architecture:
    api_fetch("doordash", "orders", {"limit": 50})
        → loads stored session token from ~/.config/g4l/tokens/<domain>.enc
        → httpx.get(endpoint)
        → if 200: return JSON + save to ~/.config/g4l/data/
        → if 401: no auto re-auth handler wired in — re-run api_config_from_curl

Token storage: Fernet-encrypted at rest (for session tokens/cookies).
Credential storage: macOS Keychain via tools/vault.py (for login credentials).
Domain configs: ~/.config/g4l/api_configs/<domain>.json
"""

from __future__ import annotations

import json
import logging
import base64
import time
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger("api_client")

_HOME = Path.home()
_TOKENS_DIR = _HOME / ".config" / "g4l" / "tokens"
_CONFIGS_DIR = _HOME / ".config" / "g4l" / "api_configs"
_DATA_DIR = _HOME / ".config" / "g4l" / "data"
_KEY_FILE = _TOKENS_DIR / ".fernet_key"

import os as _os
try:
    import httpx
except ImportError:
    httpx = None


# =========================================================================
# Encryption helpers
# =========================================================================

def _get_fernet():
    from cryptography.fernet import Fernet
    _TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    if _KEY_FILE.exists():
        key = _KEY_FILE.read_bytes()
    else:
        key = Fernet.generate_key()
        _KEY_FILE.write_bytes(key)
        _os.chmod(str(_KEY_FILE), 0o600)
    return Fernet(key)


def _save_token(domain: str, token_data: dict[str, Any]) -> None:
    f = _get_fernet()
    plain = json.dumps(token_data).encode()
    encrypted = f.encrypt(plain)
    path = _TOKENS_DIR / f"{domain}.enc"
    path.write_bytes(encrypted)
    _os.chmod(str(path), 0o600)


def _load_token(domain: str) -> dict[str, Any] | None:
    path = _TOKENS_DIR / f"{domain}.enc"
    if not path.exists():
        return None
    try:
        f = _get_fernet()
        encrypted = path.read_bytes()
        return json.loads(f.decrypt(encrypted).decode())
    except Exception:
        return None


# =========================================================================
# Domain config
# =========================================================================

def _load_config(domain: str) -> dict[str, Any] | None:
    path = _CONFIGS_DIR / f"{domain}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _save_config(domain: str, config: dict[str, Any]) -> None:
    _CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    (_CONFIGS_DIR / f"{domain}.json").write_text(json.dumps(config, indent=2))


# =========================================================================
# Token refresh — no automated re-auth handler wired in (BrowserOS removed).
# 401s require re-running api_config_from_curl with a fresh cURL capture.
# =========================================================================

_REFRESH_HANDLERS: dict[str, callable] = {}


# =========================================================================
# Core API fetch
# =========================================================================

def api_fetch(domain: str, endpoint: str, params: dict | None = None) -> str:
    """
    Call a configured API endpoint. Auto-manages tokens and retry on 401.
    Returns JSON results as a string.
    """
    if httpx is None:
        return "[api] httpx not installed. Run: pip install httpx"

    config = _load_config(domain)
    if not config:
        return (
            f"[api] No config for domain '{domain}'. "
            f"Run api_discover(domain='{domain}') to set it up.\n"
            f"Configs stored at: {_CONFIGS_DIR}"
        )

    ep = config.get("endpoints", {}).get(endpoint)
    if not ep:
        available = list(config.get("endpoints", {}).keys())
        return f"[api] Unknown endpoint '{endpoint}' for '{domain}'. Available: {available}"

    token = _load_token(domain) or {}
    auth_config = config.get("auth", {})

    url = ep["url"]
    method = ep.get("method", "GET").upper()

    client_kwargs = {}
    if params:
        if method == "GET":
            url = url + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        else:
            client_kwargs["data"] = params

    def _make_request(tok: dict, timeout_s: int = 30) -> httpx.Response:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }
        if auth_config.get("type") == "cookie":
            cookie_val = tok.get("cookie") or tok.get("all_cookies")
            if isinstance(cookie_val, dict):
                cookie_val = "; ".join(f"{k}={v}" for k, v in cookie_val.items())
            if cookie_val:
                headers["Cookie"] = cookie_val
        elif auth_config.get("type") == "bearer":
            token_val = tok.get("token") or tok.get("access_token")
            if token_val:
                headers["Authorization"] = f"Bearer {token_val}"
        elif auth_config.get("header_name"):
            token_val = tok.get("token")
            if token_val:
                headers[auth_config["header_name"]] = token_val

        with httpx.Client(timeout=timeout_s) as client:
            if method == "GET":
                return client.get(url, headers=headers)
            else:
                return client.post(url, headers=headers, **client_kwargs)

    # First attempt
    delay = config.get("rate_limit", {}).get("min_delay_s", 0.5)
    if delay:
        time.sleep(delay)

    resp = _make_request(token)
    if resp.status_code == 200:
        data = resp.json()
        _save_data(domain, endpoint, data)
        return json.dumps(data, indent=2, default=str)

    if resp.status_code in (401, 403):
        log.info("Token expired for %s, refreshing...", domain)
        refresh_handler_name = auth_config.get("refresh_flow", "")
        handler = _REFRESH_HANDLERS.get(refresh_handler_name)
        if not handler:
            return f"[api] 401 Unauthorized and no refresh handler for '{refresh_handler_name}'"
        new_token = handler(config, token)
        if not new_token:
            return f"[api] Token refresh failed for {domain}. Re-auth manually."
        _save_token(domain, new_token)
        resp = _make_request(new_token)
        if resp.status_code == 200:
            data = resp.json()
            _save_data(domain, endpoint, data)
            return json.dumps(data, indent=2, default=str)
        return f"[api] HTTP {resp.status_code} after refresh: {resp.text[:500]}"

    return f"[api] HTTP {resp.status_code}: {resp.text[:500]}"


# =========================================================================
# Data storage
# =========================================================================

def _save_data(domain: str, endpoint: str, data: dict | list) -> str:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = _DATA_DIR / f"{domain}_{endpoint}_{ts}.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    return str(path)


# =========================================================================
# Discovery tools
# =========================================================================

def api_discover(domain: str) -> str:
    """
    Manual API discovery: log into the site yourself, copy the API request as
    cURL from DevTools, we convert it to config.
    """
    _CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

    return (
        f"API Discovery for '{domain}' — manual steps:\n\n"
        f"1. Open the site in your browser and log in if needed.\n"
        f"2. Open DevTools → Network tab → filter: Fetch/XHR.\n"
        f"3. Perform the action (e.g., view orders, search products).\n"
        f"4. Find the API request → right-click → Copy → Copy as cURL.\n"
        f"5. Run: python -c \"from tools.api_client import api_config_from_curl; "
        f"api_config_from_curl('{domain}', '''PASTE CURL HERE''')\"\n\n"
        f"Or manually create: {_CONFIGS_DIR}/{domain}.json\n\n"
        f"Config format:\n"
        f"{{\n"
        f"  \"auth\": {{\n"
        f"    \"type\": \"cookie\",\n"
        f"    \"cookie_name\": \"__Secure-dd-session\",\n"
        f"    \"refresh_url\": \"https://www.{domain}.com/auth/\"\n"
        f"  }},\n"
        f"  \"endpoints\": {{\n"
        f"    \"orders\": {{\n"
        f"      \"url\": \"https://www.{domain}.com/v1/orders\",\n"
        f"      \"method\": \"GET\",\n"
        f"      \"params\": [\"limit\"]\n"
        f"    }}\n"
        f"  }},\n"
        f"  \"rate_limit\": {{\"min_delay_s\": 2, \"max_delay_s\": 5}}\n"
        f"}}"
    )


def api_config_from_curl(domain: str, curl_command: str) -> str:
    """Convert a cURL command into a domain config and save it."""
    import shlex
    from urllib.parse import urlparse, parse_qs

    try:
        args = shlex.split(curl_command)
    except ValueError:
        args = curl_command.split()

    url = ""
    method = "GET"
    headers = {}
    cookies = {}
    for i, a in enumerate(args):
        if a == "curl":
            continue
        if a.startswith("http"):
            url = a.strip("'\"")
        if a == "-X" or a == "--request":
            method = args[i + 1]
        if a == "-H" or a == "--header":
            h = args[i + 1].strip("'\"")
            if ":" in h:
                k, v = h.split(":", 1)
                headers[k.strip()] = v.strip()
        if a == "-b" or a == "--cookie":
            c = args[i + 1].strip("'\"")
            for pair in c.split("; "):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    cookies[k] = v

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.hostname}"

    # Guess auth type
    auth = {"type": "cookie", "cookie_name": "__dd-session", "refresh_url": f"{base_url}/auth"}
    if headers.get("Authorization", "").startswith("Bearer "):
        auth = {"type": "bearer", "token": headers["Authorization"].replace("Bearer ", ""), "refresh_url": f"{base_url}/login"}

    # Extract endpoint name from URL path
    path_parts = [p for p in parsed.path.split("/") if p]
    endpoint_name = path_parts[-1] if path_parts else "data"
    if endpoint_name in ("v1", "v2", "api"):
        endpoint_name = path_parts[-2] if len(path_parts) > 1 else "data"

    endpoint_path = parsed.path
    if parsed.query:
        endpoint_path += "?" + parsed.query

    config = {
        "auth": auth,
        "endpoints": {
            endpoint_name: {
                "url": f"{base_url.rstrip('/')}/{parsed.path.lstrip('/').split('?')[0]}",
                "method": method,
                "params": list(parse_qs(parsed.query).keys()) if parsed.query else [],
            }
        },
        "rate_limit": {"min_delay_s": 2, "max_delay_s": 5},
    }

    _save_config(domain, config)
    if cookies:
        _save_token(domain, {"cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())})
        # Also offer to save session in vault
        try:
            from tools.vault import vault_save
            vault_save(f"{domain}_session", {"cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()[:3])})
        except Exception:
            pass

    return f"Config saved for '{domain}':\n{json.dumps(config, indent=2)}\n\nTo use: api_fetch(domain='{domain}', endpoint='{endpoint_name}')"


def api_list_domains() -> str:
    """List all configured API domains."""
    _CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    configs = sorted(p.name.replace(".json", "") for p in _CONFIGS_DIR.glob("*.json"))
    if not configs:
        return "No API domains configured. Use api_discover(domain='...') to add one."
    lines = [f"Configured API domains ({len(configs)}):"]
    for d in configs:
        cfg = _load_config(d)
        eps = list(cfg.get("endpoints", {}).keys()) if cfg else []
        has_token = bool(_load_token(d))
        lines.append(f"  {d} — endpoints: {eps} — token: {'valid' if has_token else 'none'}")
    return "\n".join(lines)


# =========================================================================
# Schemas
# =========================================================================

API_FETCH_SCHEMA = {
    "type": "function", "function": {
        "name": "api_fetch",
        "description": (
            "Call a configured API endpoint with stored authentication tokens. "
            "No auto-refresh on 401 — re-run api_config_from_curl with a fresh cURL capture. "
            "Results are saved to ~/.config/g4l/data/<domain>_<endpoint>.json. "
            "Use api_discover first to configure a domain, then api_fetch to get data."
        ),
        "parameters": {"type": "object", "properties": {
            "domain": {"type": "string", "description": "Domain name to fetch from (e.g. 'doordash', 'uber')"},
            "endpoint": {"type": "string", "description": "Endpoint name (e.g. 'orders', 'account')"},
            "params": {"type": "object", "description": "Optional query parameters as key-value pairs", "default": {}},
        }, "required": ["domain", "endpoint"]},
    },
}

API_DISCOVER_SCHEMA = {
    "type": "function", "function": {
        "name": "api_discover",
        "description": (
            "Set up API access for a domain. Log into the site yourself, inspect the Network tab, "
            "copy an API request as cURL, and save it as a domain config."
        ),
        "parameters": {"type": "object", "properties": {
            "domain": {"type": "string", "description": "Domain name (e.g. 'doordash', 'uber')"},
        }, "required": ["domain"]},
    },
}

API_LIST_DOMAINS_SCHEMA = {
    "type": "function", "function": {
        "name": "api_list_domains",
        "description": "List all configured API domains with their endpoints and token status.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

API_CONFIG_FROM_CURL_SCHEMA = {
    "type": "function", "function": {
        "name": "api_config_from_curl",
        "description": (
            "Convert a cURL command (copied from browser DevTools Network tab) "
            "into an API domain config and save it. The first argument is the domain name, "
            "the second is the full cURL command string."
        ),
        "parameters": {"type": "object", "properties": {
            "domain": {"type": "string", "description": "Domain name (e.g. 'doordash')"},
            "curl_command": {"type": "string", "description": "Full cURL command copied from DevTools"},
        }, "required": ["domain", "curl_command"]},
    },
}

SCHEMAS = [API_FETCH_SCHEMA, API_DISCOVER_SCHEMA, API_LIST_DOMAINS_SCHEMA, API_CONFIG_FROM_CURL_SCHEMA]
