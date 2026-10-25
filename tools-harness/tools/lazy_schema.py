"""Lazy tool descriptions: advertise every tool, but not all of its prose.

Measured 2026-10-25 on the orb profile: 34 tools cost 26_807 characters of
schema, and **50% of that was `description` prose** (13_440). That is paid on
every round of every turn, while most of the text is advice for a call the model
rarely makes ("if the sender is a list, do X; when the body contains…").

So the orb is told about every tool — names, parameters, required fields and
parameter descriptions all stay exact, because those decide whether a call is
valid — but each top-level description is cut to its first ~200 characters and
the full text moves behind `tool_help(name)`. The model keeps the gist, can pull
the rest when a call is tricky, and the round-0 prompt shrinks by ~10 KB
(~2.6k tokens) with no loss of capability.

Not applied to the orchestrator/telegram/web profiles: their prompts are not the
latency-critical path, and their tool lists are curated by hand.
"""
from __future__ import annotations

import json

# Long enough to keep "what it does + when to prefer it", short enough that 34 of
# them stop being the largest thing in the request. 200 was chosen by measuring:
# first-sentence-only (2_934 chars total) cut into real instructions on tools
# whose first sentence is a preamble.
DEFAULT_LIMIT = 200

# One line for the system prompt: without it the model reads a truncated
# description as the whole story. Funded by the schema saving above.
PROMPT_NOTE = "Tool descriptions are shortened to the first sentence; tool_help(name) reads the full text."

# Descriptions that enumerate *capabilities* rather than usage hints must stay
# whole. app_task's text is the only list of recipes ('gmail.send', 'web.read');
# truncating it to 200 chars cut 'web.read' off, and the model went back to
# open_url + observe + read_page_text + update_task_plan for a job the recipe
# does in one call (measured 2026-10-25). 665 chars, and it saves a whole turn.
KEEP_FULL = frozenset({"app_task"})

TOOL_HELP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tool_help",
        "description": (
            "Return the complete description and parameter schema of one tool by name. "
            "Use it when a shortened tool description leaves the details unclear."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Tool name, e.g. 'act'."},
            },
            "required": ["name"],
        },
    },
}


def _shorten(text: str, limit: int) -> str:
    """First `limit` characters, cut on a sentence or word boundary."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    for stop in (". ", "? ", "! "):
        cut = head.rfind(stop)
        if cut >= limit // 3:  # a sentence break late enough to be worth keeping
            return head[: cut + 1].rstrip()
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).rstrip() + "…"


def compress(tools: list, limit: int = DEFAULT_LIMIT) -> list:
    """Shallow copies with shortened descriptions. Never mutates the input.

    `parameters` is copied by reference on purpose: it is the part that must stay
    byte-identical, since the runtime validates arguments against it.
    """
    out = []
    for tool in tools:
        fn = tool.get("function", {})
        desc = fn.get("description") or ""
        if len(desc) <= limit or fn.get("name") in KEEP_FULL:
            out.append(tool)
            continue
        out.append({**tool, "function": {**fn, "description": _shorten(desc, limit)}})
    return out


def tool_help(name: str = "") -> str:
    """The untruncated description plus the parameter schema, for one tool."""
    from tools import registry  # lazy: registry imports the schemas this module is part of

    by_name = {t["function"]["name"]: t["function"] for t in registry.ALL_TOOLS}
    fn = by_name.get((name or "").strip())
    if fn is None:
        import difflib

        near = difflib.get_close_matches((name or "").strip(), list(by_name), n=4, cutoff=0.6)
        hint = f" Did you mean: {', '.join(near)}?" if near else ""
        return f"[tool_help] no tool named '{name}'.{hint}"
    params = json.dumps(fn.get("parameters") or {}, ensure_ascii=False)
    return f"{fn['name']}: {fn.get('description') or ''}\nparameters: {params}"
