"""
Trace-judge model: classifies a failing tool call into a semantic category
using the local model instead of trace_candidates.py's exact-string-prefix
key. Exact-string keys split identical root causes that render slightly
different text (a different file path, a truncated message at a different
point) and can't merge them back together. The judge reads {tool, error} and
returns a short category slug + one-line reason, so semantically identical
failures cluster even when their literal text differs.

Local gemma4:12b via Ollama — zero incremental cost, matches the existing
cheap-local-classifier pattern (nomic-embed-text, qwen3.5:4b elsewhere in
this repo). Not called on the hot path; only from the mining scripts.

Disk-cached by sha256(tool+error[:300]) since the same failure repeats
verbatim across many runs — no reason to re-judge an identical string.
"""
import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_MODEL = "gemma4:12b"
_CACHE_PATH = Path(__file__).parent.parent / "data" / "trace_judge_cache.json"
_TIMEOUT = 45.0

_SYSTEM_PROMPT = (
    "You classify a failed tool call into a short root-cause category so similar "
    "failures can be grouped together, even if their exact error text differs. "
    "Respond with JSON only: {\"category\": \"short-kebab-slug\", \"reason\": \"one line\"}. "
    "The category should name the underlying cause (e.g. \"read-before-write\", "
    "\"timeout\", \"invalid-json-input\", \"blocked-by-permission\"), not restate the "
    "tool name or quote the error text verbatim."
)


def _cache_key(tool: str, error_text: str) -> str:
    return hashlib.sha256(f"{tool}:{error_text[:300]}".encode()).hexdigest()[:16]


def _load_cache() -> dict:
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2))


def judge_pattern(tool: str, error_text: str) -> str:
    """Returns f"{tool}: {category}" — same shape as the string-key fallback so
    callers can swap this in as a drop-in Counter key. Falls back to the raw
    error text (old behavior) on any failure — mining must never hard-fail
    because the judge is unavailable."""
    fallback = f"{tool}: {error_text[:100]}"

    cache = _load_cache()
    key = _cache_key(tool, error_text)
    if key in cache:
        return f"{tool}: {cache[key]['category']}"

    try:
        import ollama
        client = ollama.Client(timeout=_TIMEOUT)
        resp = client.chat(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"tool: {tool}\nerror: {error_text[:500]}"},
            ],
            format="json",
            think=False,
            options={"temperature": 0.0},
        )
        parsed = json.loads(resp["message"]["content"])
        category = str(parsed["category"]).strip()[:60]
        reason = str(parsed.get("reason", "")).strip()[:200]
    except Exception:
        log.warning("trace_judge: falling back to string key for %s", tool, exc_info=True)
        return fallback

    cache[key] = {"category": category, "reason": reason}
    _save_cache(cache)
    return f"{tool}: {category}"
