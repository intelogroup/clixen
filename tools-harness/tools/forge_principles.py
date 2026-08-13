"""
Loads forgememo's distilled principles for this project once at process
startup and formats them into a short system-prompt block — the read-side
of the trace_candidates.py -> forge save loop (scripts/trace_candidates.py
writes recurring tool failures in; this reads distilled lessons back out).

Fetched once at import time, not per-request: forge is a subprocess call,
paying that cost on every message would add latency to the hot path for a
block that only changes as fast as nightly distillation runs.
"""
import logging
import shutil
import subprocess

_log = logging.getLogger(__name__)

_FORGE_PROJECT = "clixen"
_FORGE_LIMIT = 10


def _fetch_principles_text(project: str, limit: int) -> str | None:
    forge_bin = shutil.which("forge")
    if not forge_bin:
        return None
    try:
        result = subprocess.run(
            [forge_bin, "memory", "list", "--project", project, "--limit", str(limit)],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return result.stdout
    except Exception:
        _log.warning("forge_principles: failed to fetch from forge CLI", exc_info=True)
        return None


def _parse_titles(raw: str) -> list[str]:
    """Table rows look like:
    ID  TYPE  SCORE  PROJECT  TITLE...
    <dashes>
    <uuid> pattern 0.85 clixen  Some title...
      Narrative: long text
    We only want the titles — narratives are too verbose for a system prompt.
    """
    titles = []
    for line in raw.splitlines():
        if not line.strip() or line.startswith("ID ") or set(line.strip()) == {"-"}:
            continue
        if line.startswith("  Narrative:"):
            continue
        parts = line.split(None, 4)
        if len(parts) == 5:
            titles.append(parts[4].strip())
    return titles


def load_forge_principles_block(project: str = _FORGE_PROJECT, limit: int = _FORGE_LIMIT) -> str:
    raw = _fetch_principles_text(project, limit)
    if not raw:
        return ""
    titles = _parse_titles(raw)
    if not titles:
        return ""
    lines = "\n".join(f"- {t}" for t in titles)
    return (
        "## Known lessons from prior sessions/traces (forgememo)\n"
        f"{lines}\n"
    )


# Computed once per process at import time — see module docstring.
FORGE_PRINCIPLES_BLOCK = load_forge_principles_block()
