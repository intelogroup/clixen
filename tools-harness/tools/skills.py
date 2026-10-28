"""Procedures the orb can read on demand — no schema cost until it asks.

Why not tools: every tool costs 450–900 chars of schema on every round, and the
orb prompt is ~3 200 chars total (ADR 0005). A procedure is only needed when the
task is multi-step, so it is loaded by name and paid for once.

Why not prompt text: the prompt is already at its floor, and a 1 KB procedure for
"fill a form" would be charged on every turn including the ones about the weather.

What a skill is NOT: a capability. It cannot grant authority the tools do not
already have — it sequences calls the model could already make. That is the
cloud repo's split too (skills are procedural guidance; toolkits provide access).
"""

from __future__ import annotations

import re
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
MAX_SKILL_BYTES = 12_000
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class SkillError(RuntimeError):
    """Unknown or unreadable skill. Always reported, never silently empty."""


def _paths() -> dict[str, Path]:
    if not SKILLS_DIR.is_dir():
        return {}
    return {p.stem: p for p in sorted(SKILLS_DIR.glob("*.md")) if _NAME_RE.match(p.stem)}


def list_skills() -> list[str]:
    return sorted(_paths())


def _resolve(name: str) -> Path:
    name = (name or "").strip().lower()
    if not _NAME_RE.match(name):
        raise SkillError(
            f"'{name}' is not a skill name (lowercase letters, digits and dashes). "
            f"Available: {', '.join(list_skills()) or 'none'}"
        )
    path = _paths().get(name)
    if path is None:
        raise SkillError(f"no skill named '{name}'. Available: {', '.join(list_skills()) or 'none'}")
    resolved = path.resolve()
    if not resolved.is_relative_to(SKILLS_DIR.resolve()):
        raise SkillError(f"'{name}' resolves outside the skills directory")
    return resolved


def load_skill(name: str = "") -> str:
    """Return one procedure's text, or the list of names when called with no argument."""
    if not (name or "").strip():
        names = list_skills()
        if not names:
            return "[error] no skills are installed."
        return (
            "Available procedures (call load_skill(name) to read one):\n"
            + "\n".join(f"- {n}" for n in names)
        )
    try:
        path = _resolve(name)
        raw = path.read_text(encoding="utf-8", errors="replace")
    except SkillError as exc:
        return f"[error] {exc}"
    except OSError as exc:
        return f"[error] could not read skill '{name}': {exc}"
    if len(raw.encode("utf-8")) > MAX_SKILL_BYTES:
        return (
            f"[error] skill '{name}' is larger than {MAX_SKILL_BYTES // 1000} KB — "
            "a procedure must fit in one read"
        )
    return f"## Procedure: {path.stem}\n{raw.strip()}"


LOAD_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": (
            "Load a written procedure for a multi-step job (visual page navigation, form filling, downloading a file, finding something on a long page, multi-tab research, "
            "screenshots to action, sending mail). Call with no name to list them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Procedure name; omit to list what is available",
                },
            },
        },
    },
}
