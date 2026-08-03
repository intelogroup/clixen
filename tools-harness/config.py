"""
Single chokepoint for reading secrets (API keys, tokens, passwords) from the
environment. Existing os.environ.get() call sites elsewhere aren't migrated —
this exists so *new* secret reads have one place to go, and so a future vault
swap (if ever justified) only touches this file instead of 25+ scattered ones.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def get_secret(name: str, required: bool = True) -> str | None:
    """Read a secret by env var name. Raises if required and missing."""
    value = os.environ.get(name)
    if required and not value:
        raise RuntimeError(f"Missing required secret: {name} (set it in tools-harness/.env)")
    return value
