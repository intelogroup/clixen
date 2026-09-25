"""
Oversized tool-result spill — ported from deepseek-harness's spill-policy
(packages/spill/spill-policy).

Existing behavior in ollama_client/cloud_client was head-only truncation:
result[:3800] + a notice, tail silently gone forever. This keeps the full
text on disk and gives the model a head/tail preview + a path it can
read_file() to get the rest, instead of losing the tail permanently.

SECURITY: a spilled result is raw tool output — it can contain a
credential (a vault value, an API key, a session token). It is written to
disk AND previewed back into the model's context, so both surfaces are
redacted here, before either one exists, and the file is created 0600. The
journal's write-boundary scrubber (store/run_store) handles dict-shaped
tool results; this handles free text.
"""

import os
import re
import time
from pathlib import Path

_SPILL_DIR = Path(__file__).resolve().parent.parent / ".spill"

# Credential shapes that are unmistakable regardless of the vault.
_CREDENTIAL_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"),                      # openai-style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),                  # github tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                         # AWS access keys
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),              # slack
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    re.compile(r"(?i)\b(pass(?:word|wd)?|token|api[_-]?key|secret|authorization|bearer)"
               r"(\s*[:=]\s*|\s+)(\S{6,})"),
)


def _vault_secret_values() -> frozenset[str]:
    """Known Keychain values (lazy + fail-safe: degrades to pattern-only)."""
    try:
        from store.run_store import _vault_secret_values as _vs

        return _vs()
    except Exception:
        return frozenset()


def redact(text: str, secrets: frozenset[str] | None = None) -> str:
    """Mask credentials in free text: exact vault values first, then shapes."""
    for secret in sorted(secrets if secrets is not None else _vault_secret_values(),
                         key=len, reverse=True):
        if secret and len(secret) >= 6:
            text = text.replace(secret, "[REDACTED:vault]")
    for pat in _CREDENTIAL_PATTERNS:
        if pat.groups >= 3:  # keyed form — keep the key, mask the value
            text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pat.sub("[REDACTED:credential]", text)
    return text


def spill(text: str, tool_name: str, cap: int = 4000) -> str:
    """Return `text` unchanged if within cap, else a head/tail preview + on-disk locator.

    Both the stored copy and the preview are redacted, and the stored copy is
    owner-only: a spill file can carry the raw output of a credential-bearing
    tool call."""
    if len(text) <= cap:
        return text

    safe = redact(text)
    was_redacted = safe != text

    _SPILL_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", tool_name)
    path = _SPILL_DIR / f"{safe_name}_{time.time_ns()}.txt"
    try:
        # 0600 from the start — never leave a window where the file is 0644.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(safe)
        note = " (secrets redacted)" if was_redacted else ""
        locator = (f"Full result ({len(text)} chars) saved to {path}{note} — "
                   f"read_file({path}) for the rest.")
    except OSError:
        locator = f"[spill to disk failed — showing head/tail only, {len(text)} chars total]"

    budget = max(0, cap - len(locator) - 20)
    head = budget // 2
    tail = budget - head
    preview = (safe[:head] + f"\n...[{len(safe) - budget} chars omitted]...\n" + safe[-tail:]
               if tail else safe[:head])
    return f"{preview}\n\n({locator})"
