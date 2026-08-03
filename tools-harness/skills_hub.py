"""
Skills Hub — centralized registry of agent capabilities.

Each skill is a self-contained task the agent (cloud LLM by default) can
execute autonomously. Users trigger skills by name or by natural-language query
(auto-matched). Skills dispatch to harness.run() with pre-wired tools, prompts,
and round limits.

Skills Hub vs Automation Catalog vs TASK_ROUTING:
- skills_hub: interactive, triggered from chat / user requests in real time
- automation_catalog: persistent background automations (scheduled/triggered)
- TASK_ROUTING: static config for the background job worker
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_HOME = str(Path.home())

# ---------------------------------------------------------------------------
# Skill definition
# ---------------------------------------------------------------------------


@dataclass
class Skill:
    id: str
    name: str
    description: str
    category: str
    model: str = ""  # empty = auto-route via harness (cloud primary)
    tools: list[str] = field(default_factory=list)
    system_prompt: str = ""
    max_rounds: int = 5
    trigger_keywords: list[str] = field(default_factory=list)
    trigger_regex: str = ""
    icon: str = "spark"
    parameters: dict[str, str] = field(default_factory=dict)
    external_path: str = ""


# ---------------------------------------------------------------------------
# Skill catalog — comprehensive registry of all agent capabilities
# ---------------------------------------------------------------------------

SKILLS: list[Skill] = []

# Any skill whose tool list overlaps this set automatically gets
# refresh_google_token injected and an auth-retry line added to its prompt.
_GOOGLE_TOOLS: frozenset[str] = frozenset({
    "get_latest_email", "list_emails", "read_email", "send_email",
    "list_calendar_events", "create_calendar_event",
    "list_tasks", "create_task", "complete_task",
    "list_google_docs", "read_google_doc", "create_google_doc", "append_google_doc",
    "list_google_sheets", "read_google_sheet", "create_google_sheet",
    "append_google_sheet", "list_google_forms", "run_inbox_monitor",
})

_AUTH_RETRY_LINE = (
    "If any Google tool returns a 401, 403, or authentication error: "
    "call refresh_google_token() immediately, then retry the failed tool once."
)


def _s(title: str, desc: str, category: str, tools: list[str], prompt: str,
       keywords: list[str], max_rounds: int = 5, icon: str = "spark",
       trigger_regex: str = "") -> Skill:
    # Auto-inject token refresh for all Google-touching skills
    if _GOOGLE_TOOLS.intersection(tools):
        if "refresh_google_token" not in tools:
            tools = list(tools) + ["refresh_google_token"]
        if _AUTH_RETRY_LINE not in prompt:
            prompt = prompt.strip() + "\n" + _AUTH_RETRY_LINE
    s = Skill(id=title.lower().replace(" ", "_").replace("/", "_"),
              name=title, description=desc, category=category,
              tools=tools, system_prompt=prompt.strip(),
              trigger_keywords=keywords, max_rounds=max_rounds, icon=icon,
              trigger_regex=trigger_regex)
    return s



# Category modules append to SKILLS at import time (data split out of this file
# for size; see skills_data/*.py). Import order across these doesn't matter —
# scoring treats each skill independently.
from skills_data import core as _sd_core          # noqa: E402,F401
from skills_data import automation as _sd_automation  # noqa: E402,F401
from skills_data import research_business as _sd_research_business  # noqa: E402,F401
from skills_data import knowledge_comm as _sd_knowledge_comm  # noqa: E402,F401
from skills_data import misc as _sd_misc          # noqa: E402,F401

# ---------------------------------------------------------------------------
# External skill discovery — scan gstack/superpowers/.claude skill dirs
# ---------------------------------------------------------------------------

_MAX_EXTERNAL_SKILL_FILE_BYTES = 200_000
_MAX_EXTERNAL_SKILL_INSTRUCTION_CHARS = 6000


def _strip_frontmatter(text: str) -> str:
    """Strip a leading YAML frontmatter block (--- ... ---) if present."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return text
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return text
    return parts[2].strip()


_EXTERNAL_SKILL_ROOTS = [
    os.path.expanduser("~/.claude/skills"),
    os.path.expanduser("~/.agents/skills"),
]


def _parse_skill_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter block (---...---) from a SKILL.md file."""
    import yaml
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}


_registered_external_ids: set[str] = set()


def _flatten_triggers(value) -> list[str]:
    """Frontmatter `triggers` fields come in wildly different shapes across skill
    authors — flat string, flat list, list of single-key dicts, dicts of nested
    lists (seen live in ext.agent-reach: category -> pipe-separated string, one
    category further nesting a list of per-platform dicts). Recursively flatten
    anything into a flat list of strings, since _score_skill only knows how to
    match against strings and previously crashed (AttributeError) the moment it
    hit a dict — which broke match_skill() for every skill, not just this one,
    since scoring loops over the full SKILLS list."""
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_flatten_triggers(v))
    elif isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_flatten_triggers(item))
    return out


def _register_skill_at(path: str, category: str = "external"):
    """Read a SKILL.md, parse its frontmatter, and register as a Skill."""
    stem = Path(path).parent.stem
    skill_id = f"ext.{stem}"
    if skill_id in _registered_external_ids:
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return

    meta = _parse_skill_frontmatter(raw)
    name = meta.get("name", stem)
    desc = (meta.get("description") or "")[:200]
    triggers = _flatten_triggers(meta.get("triggers"))
    instructions = _strip_frontmatter(raw)[:_MAX_EXTERNAL_SKILL_INSTRUCTION_CHARS]

    _registered_external_ids.add(skill_id)
    SKILLS.append(Skill(
        id=skill_id,
        name=name,
        description=desc,
        category=category,
        model="deepseek/deepseek-v4-flash",
        tools=[],
        system_prompt=instructions,
        trigger_keywords=triggers,
        max_rounds=10,
        external_path=path,
    ))


def _has_sub_skills(skill_dir: str) -> bool:
    """Check if dir has subdirectories containing SKILL.md (router dir pattern)."""
    if not os.path.isdir(skill_dir):
        return False
    for name in os.listdir(skill_dir):
        sub = os.path.join(skill_dir, name)
        if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "SKILL.md")):
            return True
    return False


def _register_sub_skills(skill_dir: str, category: str):
    """Register all sub-skill SKILL.md files under skill_dir with given category."""
    if not os.path.isdir(skill_dir):
        return
    for name in sorted(os.listdir(skill_dir)):
        sub_file = os.path.join(skill_dir, name, "SKILL.md")
        if os.path.isfile(sub_file):
            _register_skill_at(sub_file, category=category)


def _scan_external_skills():
    """Walk ~/.claude/skills/ and ~/.agents/skills/ for SKILL.md files.
    
    Two-pass to avoid ID collision when same skill exists at two levels
    (e.g. ~/.claude/skills/autoplan/ AND ~/.claude/skills/gstack/autoplan/).
    
    Pass 1: register sub-skills with parent dir name as category (gstack/qa → "gstack").
    Pass 2: register top-level skills, skipping router dirs and already-registered IDs.
    """
    for root in _EXTERNAL_SKILL_ROOTS:
        if not os.path.isdir(root):
            continue
        # Pass 1: sub-skills first (deeper category wins when same ID exists at top level)
        for entry in sorted(os.listdir(root)):
            skill_dir = os.path.join(root, entry)
            if not os.path.isdir(skill_dir):
                continue
            if _has_sub_skills(skill_dir):
                _register_sub_skills(skill_dir, category=entry)
        # Pass 2: top-level skills, skip router dirs and already-registered IDs
        for entry in sorted(os.listdir(root)):
            skill_dir = os.path.join(root, entry)
            if not os.path.isdir(skill_dir):
                continue
            skill_file = os.path.join(skill_dir, "SKILL.md")
            if os.path.isfile(skill_file) and not _has_sub_skills(skill_dir):
                _register_skill_at(skill_file)


_scan_external_skills()

# ---------------------------------------------------------------------------
# Skill matching — NLP-powered auto-detection
# ---------------------------------------------------------------------------

# Keywords that bleed across categories — require additional signal
_WEAK_KEYWORDS: set[str] = {"today", "tomorrow", "now", "this", "my", "show", "get", "the", "a", "an", "what", "when", "how", "is", "are", "can", "will", "create", "list"}

# High-confidence domain anchors — if these appear, they dominate weak keywords
_DOMAIN_ANCHORS: dict[str, re.Pattern] = {
    "email": re.compile(r"\b(inbox|email|mail|message|reply|draft|gmail|compose)\b", re.I),
    "calendar": re.compile(r"\b(calendar|schedule|meeting|appointment|event|agenda)\b", re.I),
    "tasks": re.compile(r"\b(task|todo|to-do|checklist|complete|done|finish|mark)\b", re.I),
    "search": re.compile(r"\b(search|look up|google|find info|news|price|weather|score|who won|latest|recent|current)\b", re.I),
    "browser": re.compile(r"\b(browse|open website|go to|navigate|visit|login to|fill form)\b", re.I),
    "files": re.compile(r"\b(files?|folders?|director(y|ies)|read|open|cat|ls|delete|rm|write|save|create file|path|clean|clutter|organize|tidy|neaten|reorganize)\b", re.I),
    "code": re.compile(r"\b(python|script|bash|shell|terminal|execute|run code|run command|command)\b", re.I),
    "git": re.compile(r"\b(git|commit|branch|stage|diff|push|pull|stash)\b", re.I),
    "docs": re.compile(r"\b(document|pdf|docx|markdown|convert|export|summarize|ocr|extract text|screenshot)\b", re.I),
    "telegram": re.compile(r"\b(telegram|send me|message me|ping me|notify me)\b", re.I),
    "admin": re.compile(r"\b(automation|background|schedule|workflow|setup|configure|reminder)\b", re.I),
    "monitor": re.compile(r"\b(watch|monitor|track|alert me|keep an eye)\b", re.I),
    # New categories
    "research": re.compile(r"\b(arxiv|pubmed|research|paper|literature|scientific|academic|science|journal)\b", re.I),
    "transit": re.compile(r"\b(bus|mbta|subway|transit|train schedule|directions to|how to get to|public transit|commute|t stop|bus stop|orange line|red line|green line|blue line)\b", re.I),
    "business": re.compile(r"\b(client|customer|invoice|leads?|revenue|expense|subscription|contract|deal)\b", re.I),
    "knowledge": re.compile(r"\b(notes?|notebook|save to|compile|drive|google doc|google sheet|research report)\b", re.I),
    "communication": re.compile(r"\b(whatsapp|cross.platform|broadcast|brief me|all channels|everywhere)\b", re.I),
}


def _stem(word: str) -> str:
    """Ponytail stemmer: strip common English suffixes for rougher matching."""
    if len(word) < 5:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("ying"):
        return word[:-4] + "y"
    if word.endswith("ing"):
        return word[:-3]
    if word.endswith("ed"):
        return word[:-2]
    if word.endswith("ly"):
        return word[:-2]
    if word.endswith("es") and len(word) > 5:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _score_skill(skill: Skill, query_lower: str) -> float:
    """Score how well a skill matches a user query. Higher = better match."""
    score = 0.0

    # Keyword match (weighted by specificity)
    for kw in skill.trigger_keywords:
        kw_lower = kw.lower()
        if kw_lower in query_lower:
            if kw_lower not in _WEAK_KEYWORDS:
                score += 2.0 if " " in kw_lower else 1.0  # multi-word = higher signal
            else:
                score += 0.3
        else:
            # ponytail: token overlap — partial match when exact phrase doesn't substring
            # e.g. "test website" triggers "test the site" via "test" overlap
            # ponytail: weak keywords (the/a/this/my) filtered out to avoid noise
            kw_tokens = [t for t in kw_lower.split() if t not in _WEAK_KEYWORDS]
            query_tokens = [t for t in query_lower.split() if t not in _WEAK_KEYWORDS]
            if set(kw_tokens) & set(query_tokens):
                score += 0.5

    # Description token overlap (with stemming) — catches "inconsistencies" → "inconsistency"
    # ponytail: description is richer than triggers; even partial signal helps recall
    _clean = lambda t: t.strip(".,;:!?\"'()[]{}")
    desc_tokens_stemmed = {_stem(_clean(t)) for t in skill.description.lower().split()
                          if _clean(t) not in _WEAK_KEYWORDS and len(_clean(t)) >= 3}
    query_tokens_stemmed = {_stem(_clean(t)) for t in query_lower.split()
                           if _clean(t) not in _WEAK_KEYWORDS and len(_clean(t)) >= 3}
    desc_overlap = desc_tokens_stemmed & query_tokens_stemmed
    if desc_overlap:
        score += 0.3 * len(desc_overlap)

    # Domain anchor bonus — if the query has a domain anchor, boost skills in that domain
    category_lower = skill.category.lower()
    for domain, pattern in _DOMAIN_ANCHORS.items():
        if pattern.search(query_lower):
            domain_to_category = {
                "email": "inbox", "calendar": "calendar", "tasks": "tasks",
                "search": "search", "browser": "browser", "files": "files",
                "code": "code", "git": "git", "docs": "documents",
                "telegram": "messaging", "admin": "admin", "monitor": "workflows",
                "research": "research", "business": "business",
                "knowledge": "knowledge", "communication": "communication",
            }
            mapped = domain_to_category.get(domain, domain)
            if category_lower == domain or category_lower == mapped:
                score += 3.0
                # Don't break — a query may match multiple domains (e.g. "track leads"
                # matches both "monitor" and "business")

    # Regex match if defined
    if skill.trigger_regex:
        try:
            if re.search(skill.trigger_regex, query_lower):
                score += 5.0
        except re.error:
            pass

    # Higher round count = more complex skill = penalty for very short queries
    # ponytail: 30→15 chars; real queries like "review the design" (17) are specific enough
    if len(query_lower) < 15 and skill.max_rounds > 6:
        score -= 1.0

    return score


def match_skill(query: str, min_score: float = 1.0) -> Skill | None:
    """Find the best matching skill for a user query. Returns None if no clear match."""
    query_lower = query.lower()
    best_skill = None
    best_score = 0.0

    for skill in SKILLS:
        sc = _score_skill(skill, query_lower)
        if sc > best_score:
            best_score = sc
            best_skill = skill

    if best_skill and best_score >= min_score:
        return best_skill
    return None


def get_skill(skill_id: str) -> Skill | None:
    """Get a skill by its ID."""
    for s in SKILLS:
        if s.id == skill_id:
            return s
    return None


def list_skills(category: str = "") -> list[dict]:
    """List all skills, optionally filtered by category."""
    skills = SKILLS
    if category:
        skills = [s for s in skills if s.category.lower() == category.lower()]
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "category": s.category,
            "tools": s.tools,
            "max_rounds": s.max_rounds,
            "icon": s.icon,
            "params": s.parameters,
        }
        for s in skills
    ]


def list_categories() -> list[str]:
    """List all unique skill categories."""
    seen = set()
    result = []
    for s in SKILLS:
        if s.category not in seen:
            seen.add(s.category)
            result.append(s.category)
    return result


# ---------------------------------------------------------------------------
# Skill dispatch — runs via harness.run()
# ---------------------------------------------------------------------------


def dispatch(skill_id: str, user_message: str, chat_id: str = None,
             on_token: Callable[[str], None] = None,
             images: list = None) -> tuple[str, str]:
    """
    Dispatch a skill to the local agent.

    Returns (result_text, model_used).
    """
    skill = get_skill(skill_id)
    if skill is None:
        raise KeyError(f"Unknown skill: {skill_id!r}")

    import harness

    return harness.run(
        query=user_message,
        model=skill.model or None,
        tools=skill.tools,
        chat_id=chat_id,
        max_rounds=skill.max_rounds,
        on_token=on_token,
        images=images,
    )


def _extract_params_from_schema(user_message: str,
                                 schema: dict) -> dict:
    """Parse the user message into tool params using the tool's JSON schema.
    Uses simple pattern matching based on param names/types — no LLM needed.
    Works for ANY registered tool automatically.
    """
    import re as _re
    props = schema["function"]["parameters"].get("properties", {})
    param_names = list(props.keys())
    params: dict = {}

    for name in param_names:
        info = props[name]
        ptype = info.get("type", "string")

        # String params: try to extract from common patterns
        if ptype == "string":
            desc = (info.get("description") or "").lower()
            enum_values = info.get("enum")

            # Enum params: match one of the allowed values as a substring of
            # the message (case-insensitive). Never guess — an unmatched enum
            # param is left unset rather than filled with the raw message,
            # which would violate the enum and silently break the tool call.
            if enum_values:
                lowered = user_message.lower()
                for val in enum_values:
                    if val and val.lower() in lowered:
                        params[name] = val
                        break
                continue

            # "origin/destination" pattern: "from X to Y"
            if name in ("origin", "source", "from", "address"):
                m = _re.search(r"(?:from|at|near)\s+(.+?)(?:\s+to\s+|$)", user_message, _re.I)
                if m:
                    params[name] = m.group(1).strip().rstrip(",")

            elif name in ("destination", "dest", "target", "to", "goal"):
                m = _re.search(r".*\b(to|for|toward|via)\s+(.+?)$", user_message, _re.I)
                if m:
                    dest = m.group(2).strip().rstrip(",")
                    # Strip trailing time qualifiers like "in 10 min"
                    dest = _re.sub(r"\s+in\s+\d+\s*(?:min(?:ute)?s?|sec(?:ond)?s?|hr(?:s)?|hour(?:s)?).*$", "", dest, flags=_re.I).strip()
                    if dest:
                        params[name] = dest

            elif name in ("query", "q", "search", "text", "content", "message"):
                params[name] = user_message

            elif name in ("url", "link", "website"):
                m = _re.search(r"https?://\S+", user_message)
                if m:
                    params[name] = m.group(0)

            elif (name in ("path", "file", "filename", "directory", "dir")
                  or name.endswith(("_path", "_file", "_dir", "_directory"))):
                m = _re.search(r"(?:/[\w.\-/]+|~[\w.\-/]+)", user_message)
                if m:
                    params[name] = m.group(0)

            elif name in ("email", "mail", "address"):
                m = _re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", user_message)
                if m:
                    params[name] = m.group(0)

            elif name in ("name", "title", "label", "subject"):
                params[name] = user_message

            # Any remaining string param that hasn't been caught
            else:
                if not params:
                    params[name] = user_message

        # Integer params: context-aware extraction
        elif ptype in ("integer", "number"):
            # Time-related params: "in X min" / "after X minutes"
            if name in ("depart_in", "timeout", "delay", "interval", "duration"):
                m = _re.search(r"(?:in|after|wait)\s+(\d+)\s*(?:min|minutes?|sec|seconds?|hr|hours?|day|days?)",
                               user_message, _re.I)
                if m:
                    params[name] = int(m.group(1))
            # Count/limit params: "X items" / "top X" / "last X"
            elif name in ("count", "limit", "max", "max_results", "n", "num"):
                m = _re.search(r"(?:top|last|first|recent|latest|next|count|limit|n=?=?)\s*:?\s*(\d+)",
                               user_message, _re.I)
                if m:
                    params[name] = int(m.group(1))
            # Fallback: first bare number in message
            else:
                m = _re.search(r"\b(\d+)\b", user_message)
                if m:
                    params[name] = int(m.group(1))

        # Boolean params: check for presence
        elif ptype == "boolean":
            params[name] = True  # presence = True

    return params


def dispatch_with_prompt(skill_id: str, user_message: str,
                         chat_id: str = None,
                         on_token: Callable[[str], None] = None,
                         images: list = None) -> tuple[str, str]:
    """
    Dispatch a skill. For single-tool skills, extracts parameters using
    the tool's JSON schema, then calls the executor directly — no gemma4,
    no web search, no tiny model needed.

    For multi-tool skills, goes through harness.run() for LLM orchestration.
    """
    skill = get_skill(skill_id)
    if skill is None:
        raise KeyError(f"Unknown skill: {skill_id!r}")

    # External skills: delegate to load_external_skill (reads SKILL.md, full toolset)
    if skill.external_path:
        result_str = load_external_skill(
            {"path": skill.external_path, "message": user_message}
        )
        return result_str, skill.model, "external"

    # Single-tool skills: extract params + call executor directly
    if len(skill.tools) == 1:
        tool_name = skill.tools[0]
        from tools.registry import EXECUTORS, ALL_TOOLS
        executor = EXECUTORS.get(tool_name)
        schema = next(
            (t for t in ALL_TOOLS if t["function"]["name"] == tool_name),
            None,
        )
        if executor and schema:
            params = _extract_params_from_schema(user_message, schema)
            required = schema["function"]["parameters"].get("required", [])
            missing = [r for r in required if r not in params]
            if not missing:
                result = executor(params)
                if not isinstance(result, str):
                    result = str(result)
                return result, skill.model, "direct_tool"
            # Couldn't confidently extract a required param — don't call the
            # executor with a guess. Fall through to the LLM tool loop below,
            # which can reason about context (conversation history, images)
            # that this regex extractor can't see.

    # Multi-tool skills: go through harness LLM tool loop. Instructions carry
    # at system-role weight (extra_system_prompt) rather than being smuggled
    # into the user message, so they hold the same authority as the rest of
    # the assembled system prompt (date/memory/base instructions).
    extra_system_prompt = (
        f"[SKILL: {skill.name} — {skill.description}]\n\n{skill.system_prompt}"
        if skill.system_prompt else None
    )

    import harness

    result, actual_model, _intent = harness.run(
        query=user_message,
        model=skill.model or None,
        tools=skill.tools,
        chat_id=chat_id,
        max_rounds=skill.max_rounds,
        on_token=on_token,
        images=images,
        extra_system_prompt=extra_system_prompt,
    )
    return result, actual_model, "agent_loop"


# ---------------------------------------------------------------------------
# Skill discovery — called by the agent itself via tools
# ---------------------------------------------------------------------------

SKILLS_DISCOVERY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_available_skills",
        "description": (
            "List all available agent skills that you can use to help the user. "
            "Call this when the user wants to know what you can do, or when you're unsure "
            "which tool to use for a task. Returns skill names, descriptions, and required tools."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional category filter: Inbox, Calendar, Tasks, Search, Browser, Files, Code, Git, Documents, Messaging, Workflows, Admin, gstack, external",
                },
            },
        },
    },
}


def list_available_skills(args: dict) -> str:
    """Tool executor: list skills for the agent to reference."""
    category = args.get("category", "")
    skills = list_skills(category)
    if not skills:
        return "No skills found."
    lines = ["Available skills:"]
    for s in skills:
        lines.append(
            f"• {s['name']} [{s['category']}]\n"
            f"  {s['description']}\n"
            f"  Tools: {', '.join(s['tools'][:5])}"
            + ("..." if len(s['tools']) > 5 else "") + "\n"
            f"  Max rounds: {s['max_rounds']}"
        )
    return "\n".join(lines)


SKILLS_MATCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "match_skill_for_task",
        "description": (
            "Given a user's request, find the best matching skill for the task. "
            "Returns the skill name, description, required tools, and instructions. "
            "Call this before dispatching a skill to find the right one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The user's request to find a matching skill for",
                },
            },
            "required": ["query"],
        },
    },
}


def match_skill_for_task(args: dict) -> str:
    """Tool executor: find the best skill for a user task."""
    query = args.get("query", "")
    skill = match_skill(query)
    if skill is None:
        return "No matching skill found. Try being more specific or use list_available_skills to see all options."
    return (
        f"Best match: {skill.name}\n"
        f"Description: {skill.description}\n"
        f"Required tools: {', '.join(skill.tools)}\n"
        f"Max rounds: {skill.max_rounds}\n"
        f"Instructions:\n{skill.system_prompt[:500]}"
    )


RUN_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_skill",
        "description": (
            "Execute a predefined skill by id — dispatches to a fresh sub-agent "
            "pre-wired with that skill's tools, model, system prompt, and round limit. "
            "Call match_skill_for_task first to find the right skill_id, then call this "
            "to actually run it instead of trying to reproduce its steps yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "The exact skill id, e.g. from match_skill_for_task's output.",
                },
                "message": {
                    "type": "string",
                    "description": "The user's original request, passed as-is into the skill's sub-agent.",
                },
            },
            "required": ["skill_id", "message"],
        },
    },
}


def run_skill(args: dict) -> str:
    """Tool executor: actually dispatch a skill to its own sub-agent, from
    inside the main agent's own tool loop (unlike match_skill_for_task, which
    only returns descriptive text)."""
    skill_id = args.get("skill_id", "")
    message = args.get("message", "")
    if not skill_id or not message:
        return "[error] run_skill requires both skill_id and message"
    if get_skill(skill_id) is None:
        ids = ", ".join(s["id"] for s in list_skills())
        return f"[error] unknown skill_id {skill_id!r}. Available ids: {ids}"

    from tools.registry import CURRENT_CHAT_ID
    chat_id = CURRENT_CHAT_ID.get()
    try:
        result, _model, _source = dispatch_with_prompt(skill_id, message, chat_id=chat_id)
        return result
    except Exception as e:
        return f"[error] run_skill({skill_id!r}) failed: {e}"


LOAD_EXTERNAL_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "load_external_skill",
        "description": (
            "FALLBACK: load and run an external SKILL.md file by filesystem path. "
            "PREFER run_skill first (it dispatches with correct tools/model/rounds). "
            "Use this only for skills NOT registered in the skills hub (e.g. a skill "
            "you found in ~/.claude/skills/<name>/SKILL.md that has no ext.* id). "
            "Strips the YAML frontmatter, feeds the instructions body as guidance to "
            "a fresh sub-agent with your own full toolset. Best-effort: any step naming "
            "a tool you don't have (Claude-only tools like AskUserQuestion or "
            "mcp__claude-in-chrome__*) will be skipped or approximated with your own "
            "equivalent tool, not simulated."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the SKILL.md file to load.",
                },
                "message": {
                    "type": "string",
                    "description": "The user's request to execute using this skill's instructions.",
                },
            },
            "required": ["path", "message"],
        },
    },
}

def load_external_skill(args: dict) -> str:


    """Tool executor: read an external SKILL.md and run its instructions as a
    sub-agent system prompt against the user's message. Unlike run_skill (which
    only knows clixen's own 66 native skills), this can load any Claude Code
    skill or plugin skill file, since those are just markdown text — no
    Claude-specific runtime dependency to bridge."""
    path = args.get("path", "")
    message = args.get("message", "")
    if not path or not message:
        return "[error] load_external_skill requires both path and message"

    expanded = os.path.expanduser(path)
    if not os.path.isfile(expanded):
        return f"[error] file not found: {expanded}"
    size = os.path.getsize(expanded)
    if size > _MAX_EXTERNAL_SKILL_FILE_BYTES:
        return f"[error] skill file too large ({size} bytes) — refusing to load"

    try:
        with open(expanded, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        return f"[error] could not read {expanded}: {e}"

    instructions = _strip_frontmatter(raw).strip()
    if not instructions:
        return f"[error] {expanded} has no usable content after stripping frontmatter"
    if len(instructions) > _MAX_EXTERNAL_SKILL_INSTRUCTION_CHARS:
        instructions = instructions[:_MAX_EXTERNAL_SKILL_INSTRUCTION_CHARS] + "\n...[truncated]"

    augmented_message = (
        f"[EXTERNAL SKILL loaded from {expanded}]\n\n"
        "INSTRUCTIONS (best-effort — this skill file was written for a different "
        "agent's tool ecosystem. Skip any step naming a tool you don't have "
        "(e.g. Claude-only tools like AskUserQuestion or mcp__claude-in-chrome__*), "
        "or approximate it with your own equivalent tool instead:\n"
        f"{instructions}\n\n"
        f"USER REQUEST:\n{message}"
    )

    import harness
    from tools.registry import CURRENT_CHAT_ID
    chat_id = CURRENT_CHAT_ID.get()
    try:
        result, _model, _intent = harness.run(query=augmented_message, chat_id=chat_id, max_rounds=8)
        return result
    except Exception as e:
        return f"[error] load_external_skill({expanded!r}) failed: {e}"


GSTACK_SKILL_INFO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "gstack_skill_info",
        "description": (
            "Return frontmatter metadata (triggers, description, category, "
            "allowed-tools, icon) for a registered external skill by skill_id. "
            "Use when orchestrator needs to check whether a gstack/claude/agent "
            "skill matches the user's request before dispatching."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": (
                        "Skill ID as shown in list_skills(). "
                        "Examples: ext.qa, ext.autoplan, ext.investigate"
                    ),
                },
            },
            "required": ["skill_id"],
        },
    },
}

def gstack_skill_info(args: dict) -> str:
    """Tool executor: return frontmatter metadata for a registered external skill."""
    skill_id = args.get("skill_id", "").strip()
    if not skill_id:
        return "[error] skill_id required"

    skill = get_skill(skill_id)
    if skill is None:
        ids = ", ".join(s["id"] for s in list_skills() if s["id"].startswith("ext."))
        return f"[error] unknown skill_id {skill_id!r}. Available external ids: {ids}"

    if not skill.external_path:
        return f"[error] {skill_id} has no external_path (not an external skill)"

    try:
        with open(skill.external_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        return f"[error] could not read {skill.external_path}: {e}"

    meta = _parse_skill_frontmatter(raw)
    triggers = meta.get("triggers") or meta.get("trigger_keywords") or []
    allowed_tools = meta.get("allowed-tools") or meta.get("tools") or []
    description = (meta.get("description") or skill.description)[:500]
    name = meta.get("name", skill.name)

    return (
        f"skill_id: {skill_id}\n"
        f"name: {name}\n"
        f"category: {skill.category}\n"
        f"description: {description}\n"
        f"triggers: {', '.join(triggers) if triggers else '(none)'}\n"
        f"allowed_tools: {', '.join(allowed_tools) if allowed_tools else '(inherited from agent)'}\n"
        f"path: {skill.external_path}\n"
        f"model: {skill.model}\n"
        f"max_rounds: {skill.max_rounds}\n"
    )


# Both schemas and executors for registry.py
SKILLS_HUB_SCHEMAS = [SKILLS_DISCOVERY_SCHEMA, SKILLS_MATCH_SCHEMA, RUN_SKILL_SCHEMA,
                       LOAD_EXTERNAL_SKILL_SCHEMA, GSTACK_SKILL_INFO_SCHEMA]

SKILLS_HUB_EXECUTORS = {
    "list_available_skills": list_available_skills,
    "match_skill_for_task": match_skill_for_task,
    "run_skill": run_skill,
    "load_external_skill": load_external_skill,
    "gstack_skill_info": gstack_skill_info,
}
