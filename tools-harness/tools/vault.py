"""
Credential vault — stores and retrieves service credentials securely.

Backend:
    macOS Keychain — encrypted at OS level, protected by login password.

Usage (agent tools):
    vault_save("doordash", {"username": "me@email.com", "password": "..."})
    vault_get("doordash")    → dict as formatted string
    vault_list()             → all stored services
    vault_delete("doordash")
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys

log = logging.getLogger("vault")

# =========================================================================
# macOS Keychain backend
# =========================================================================

import shutil
_KEYCHAIN_AVAILABLE = shutil.which("security") is not None and sys.platform == "darwin"


def _kc_save(service: str, data: dict) -> str | None:
    pwd = json.dumps(data, default=str)
    subprocess.run(["security", "delete-generic-password", "-s", service], capture_output=True, text=True)
    r = subprocess.run(
        ["security", "add-generic-password",
         "-s", service, "-a", service, "-w", pwd,
         "-l", f"g4l: {service}",
         "-j", "Managed by g4l agent vault"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return r.stderr.strip() or r.stdout.strip()
    return None


def _kc_get(service: str) -> dict | None:
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout.strip())
    except (json.JSONDecodeError, TypeError):
        return None


def _kc_list() -> list[str]:
    r = subprocess.run(["security", "dump-keychain"], capture_output=True, text=True)
    services = []
    for line in r.stdout.split("\n"):
        if '"g4l: ' in line:
            svc = line.split('"g4l: ')[-1].rstrip('"')
            if svc:
                services.append(svc)
    return sorted(set(services))


def _kc_delete(service: str) -> str | None:
    r = subprocess.run(["security", "delete-generic-password", "-s", service], capture_output=True, text=True)
    if r.returncode != 0:
        return r.stderr.strip() or r.stdout.strip()
    return None


# =========================================================================
# Unified API
# =========================================================================


def _check_keychain() -> str | None:
    if not _KEYCHAIN_AVAILABLE:
        return "[vault] macOS Keychain not available (requires macOS + security CLI)"
    return None


def vault_save(service: str, data: str | dict) -> str:
    if err := _check_keychain():
        return err
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {"value": data}
    elif not isinstance(data, dict):
        data = {"value": str(data)}
    err = _kc_save(service, data)
    if err:
        return f"[vault] Error saving '{service}': {err}"
    fields = ", ".join(
        f"{k}={v}" if len(str(v)) < 30 else f"{k}=<{len(str(v))} chars>"
        for k, v in data.items()
    )
    return f"Saved credentials for '{service}' in Keychain: {fields}"


def vault_get(service: str) -> str:
    if err := _check_keychain():
        return err
    data = _kc_get(service)
    if data is None:
        return f"Credentials for '{service}' not found. Save them with vault_save."
    lines = [f"Credentials for '{service}':"]
    for k, v in data.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def vault_list() -> str:
    if err := _check_keychain():
        return err
    services = _kc_list()
    if not services:
        return "No credentials stored. Use vault_save to add some."
    lines = [f"Stored credentials ({len(services)}):"]
    for svc in services:
        data = _kc_get(svc)
        fields = list((data or {}).keys())
        has_pwd = "password" in (data or {})
        lines.append(f"  {svc:20s} fields: {fields} {'[has password]' if has_pwd else ''}")
    return "\n".join(lines)


def vault_delete(service: str) -> str:
    if err := _check_keychain():
        return err
    err = _kc_delete(service)
    if err:
        return f"[vault] Delete failed for '{service}': {err}"
    return f"Deleted credentials for '{service}'."


# =========================================================================
# Schemas
# =========================================================================

VAULT_SAVE_SCHEMA = {
    "type": "function", "function": {
        "name": "vault_save",
        "description": (
            "Save credentials for a service (DoorDash, Uber, etc.) in the macOS Keychain vault. "
            "Credentials are encrypted at OS level, protected by your login password. "
            "Use this when a user provides their login info for a service."
        ),
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string", "description": "Service name (e.g. 'doordash', 'uber', 'dunkin')"},
            "data": {"type": "object", "description": "Credential fields as key-value pairs (e.g. {\"username\": \"me@email.com\", \"password\": \"mypass\"})"},
        }, "required": ["service", "data"]},
    },
}

VAULT_GET_SCHEMA = {
    "type": "function", "function": {
        "name": "vault_get",
        "description": (
            "Retrieve stored credentials for a service from the macOS Keychain. "
            "Returns all stored fields (username, password, tokens, etc.). "
            "Use this when you need to log into a service on behalf of the user."
        ),
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string", "description": "Service name (e.g. 'doordash', 'uber')"},
        }, "required": ["service"]},
    },
}

VAULT_LIST_SCHEMA = {
    "type": "function", "function": {
        "name": "vault_list",
        "description": "List all services that have credentials stored in the macOS Keychain vault (shows service names and field types, never exposes passwords).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

VAULT_DELETE_SCHEMA = {
    "type": "function", "function": {
        "name": "vault_delete",
        "description": "Delete stored credentials for a service from the macOS Keychain vault.",
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string", "description": "Service name to delete"},
        }, "required": ["service"]},
    },
}

SCHEMAS = [VAULT_SAVE_SCHEMA, VAULT_GET_SCHEMA, VAULT_LIST_SCHEMA, VAULT_DELETE_SCHEMA]
