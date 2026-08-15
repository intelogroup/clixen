"""
Oversized tool-result spill — ported from deepseek-harness's spill-policy
(packages/spill/spill-policy).

Existing behavior in ollama_client/cloud_client was head-only truncation:
result[:3800] + a notice, tail silently gone forever. This keeps the full
text on disk and gives the model a head/tail preview + a path it can
read_file() to get the rest, instead of losing the tail permanently.
"""

import re
import time
from pathlib import Path

_SPILL_DIR = Path(__file__).resolve().parent.parent / ".spill"


def spill(text: str, tool_name: str, cap: int = 4000) -> str:
    """Return `text` unchanged if within cap, else a head/tail preview + on-disk locator."""
    if len(text) <= cap:
        return text

    _SPILL_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", tool_name)
    path = _SPILL_DIR / f"{safe_name}_{time.time_ns()}.txt"
    try:
        path.write_text(text, encoding="utf-8")
        locator = f"Full result ({len(text)} chars) saved to {path} — read_file({path}) for the rest."
    except OSError:
        locator = f"[spill to disk failed — showing head/tail only, {len(text)} chars total]"

    budget = max(0, cap - len(locator) - 20)
    head = budget // 2
    tail = budget - head
    preview = text[:head] + f"\n...[{len(text) - budget} chars omitted]...\n" + text[-tail:] if tail else text[:head]
    return f"{preview}\n\n({locator})"
