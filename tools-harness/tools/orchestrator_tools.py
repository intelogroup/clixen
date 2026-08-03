"""
Subagent tools for the primary Orchestrator Agent.

Filesystem tools added (2026-07):
  ask_read_file     — read content of any file
  ask_write_file    — create/overwrite a file with content
  ask_delete_file   — delete a file or directory
  ask_rename_file   — rename/move a file or folder
  ask_run_command   — run an arbitrary bash command
"""

import contextvars as _contextvars
import re

_AGENT_ID: _contextvars.ContextVar[str] = _contextvars.ContextVar("agent_id", default="orchestrator")


# ─── Schemas ──────────────────────────────────────────────────────────────────

ASK_LOCAL_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_local_agent",
        "description": (
            "Invoke the local filesystem agent for complex multi-step file operations: "
            "listing directories, searching, copying, moving, editing, running python, or parsing/filling PDF/DOCX forms. "
            "For simple single-step operations prefer ask_read_file, ask_write_file, ask_delete_file, ask_rename_file, or ask_run_command."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The exact query/instructions for the filesystem/local agent. Must be completely resolved and self-contained.",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_read_file",
        "description": "Read the content of a file at an absolute path and return it as text.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to read (e.g. /Users/kalinovdameus/Documents/resume.pdf).",
                }
            },
            "required": ["path"],
        },
    },
}

ASK_WRITE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_write_file",
        "description": "Create or overwrite a file at an absolute path with the given text content.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to create or overwrite.",
                },
                "content": {
                    "type": "string",
                    "description": "The text content to write to the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
}

ASK_DELETE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_delete_file",
        "description": "Delete a file or directory at the given absolute path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file or directory to delete.",
                }
            },
            "required": ["path"],
        },
    },
}

ASK_RENAME_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_rename_file",
        "description": "Rename or move a file or directory from source to destination.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Absolute path of the file/directory to rename or move.",
                },
                "destination": {
                    "type": "string",
                    "description": "Absolute path of the new name/location.",
                },
            },
            "required": ["source", "destination"],
        },
    },
}

ASK_RUN_COMMAND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_run_command",
        "description": (
            "Run a bash shell command on this computer and return stdout/stderr. "
            "Use for: listing directories (ls), searching (find/grep), moving files (mv), "
            "running scripts, getting disk usage, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The exact bash command to run (e.g. 'ls -la ~/Documents/').",
                }
            },
            "required": ["command"],
        },
    },
}

ASK_WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_web_search",
        "description": "Invoke the search subagent to perform an internet search for recent info, schedules, scores, prices, weather/forecasts, etc. Use this for weather questions, including detailed ones (specific address/zip/date) — do NOT use ask_research_agent for weather, its multi-round scrape chain takes 30-200+ seconds vs a few seconds here for the same answer.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The exact search query. Make sure to include relevant years/terms if time-sensitive.",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_BROWSER_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_browser_agent",
        "description": "Invoke the browser automation agent to navigate websites, click, type, fill forms, or interact with web applications (e.g. DoorDash, Uber, banking).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Self-contained browser automation instructions (e.g., 'Log into DoorDash and search for sushi').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_MACOS_NATIVE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_macos_native",
        "description": "Invoke the macOS native subagent to read/write system clipboard, list Safari tabs, manage macOS Notes, or run AppleScript.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Instructions for native macOS utilities.",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_EMAIL_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_email_agent",
        "description": "Invoke the email subagent to check, read, summarize, or send emails.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Instructions for email actions (e.g., 'Draft an email to Bob').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_TASKS_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_tasks_agent",
        "description": "Invoke the tasks/reminders subagent to create, complete, list, or delete tasks and reminders.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Instructions for task actions (e.g., 'Add buy groceries to my task list').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_CALENDAR_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_calendar_agent",
        "description": "Invoke the calendar subagent to create, list, edit, or delete calendar events.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Instructions for calendar actions (e.g., 'Schedule meeting with Louise tomorrow').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_DOCS_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_docs_agent",
        "description": "Invoke the Google Docs subagent to create, read, list, or append to Google Docs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Instructions for Docs actions (e.g., 'Create a doc titled Meeting Notes with...').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_SHEETS_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_sheets_agent",
        "description": "Invoke the Google Sheets subagent to create, read, list, append rows to, or update cells in Google Sheets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Instructions for Sheets actions (e.g., 'Add a row with today's sales to my Sheet').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_DEEP_RESEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_deep_research",
        "description": "Invoke the deep research subagent to perform recursive, multi-step, parallel search and compile a comprehensive report on a complex topic.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The research query or topic."},
                "depth": {"type": "integer", "description": "Research depth (1-3, default 2).", "default": 2},
                "breadth": {"type": "integer", "description": "Research breadth (1-5, default 3).", "default": 3},
                "time_budget_seconds": {"type": "integer", "description": "Wall-clock budget in seconds before less-critical phases (gap analysis, critique rounds, CRAAP eval) are skipped. Default 270. Raise for depth=3/breadth=5 requests if 270s isn't enough — stay under 300 to avoid the chat stream's stall timeout.", "default": 270},
            },
            "required": ["query"],
        }
    }
}

ASK_AUTOMATION_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_automation_agent",
        "description": (
            "Fallback for complex/ambiguous automation requests only. create_automation, "
            "list_automations, pause_automation, resume_automation, update_automation, "
            "delete_automation, and trigger_automation_now are all directly available — "
            "call them yourself instead of delegating here for anything that fits one of "
            "them. Only use this subagent for delete_workflow_permanently (irreversible "
            "hard-delete) or a request too ambiguous to map to a single direct tool call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Instructions for automation actions (e.g., 'pause my email watcher', 'list my automations').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_REDDIT_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_reddit_agent",
        "description": (
            "Query accumulated Reddit market-intelligence insights (pain points, "
            "market gaps, opportunities) gathered from background monitoring of "
            "business/SaaS subreddits. Reads the existing insight database — does "
            "NOT trigger a live Reddit scan (that runs on its own schedule)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for, e.g. 'pain points about SaaS pricing' or 'newest opportunities this week'.",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_X_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_x_agent",
        "description": (
            "Search X for posts by keyword/topic, read a specific post, or get "
            "an account's recent posts using the configured twscrape account."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The complete X search or reading request.",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_SCIENCE_SCOUT_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_science_scout_agent",
        "description": (
            "Query accumulated science-niche findings (e.g. microplastic-degrading "
            "enzymes) gathered from background monitoring of arXiv/journal search, "
            "each graded by evidence level (Observed/Replicated/Mechanistically "
            "supported/Predicted/Speculative/Impossible). Reads the existing claim "
            "database — does NOT trigger a live scan (that runs on its own schedule)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for, e.g. 'new PETase enzyme findings' or 'strongest evidence-level claims this month'.",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_DEV_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_dev_agent",
        "description": "Invoke the dev subagent for git operations (status, diff, log, add, commit, checkout, new worktree) or REPL/window inspection (kernel vars, reset kernel, list windows, screenshot).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Instructions for git/repl/dev actions (e.g., 'commit these changes with message...', 'show git status').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_MESSAGING_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_messaging_agent",
        "description": "Invoke the messaging subagent to search or send iMessage/WhatsApp, search Slack, or set a reminder.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Instructions for messaging actions (e.g., 'search iMessage for pizza plans', 'send WhatsApp to Marc').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_RESEARCH_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_research_agent",
        "description": "Invoke the research-connectors subagent for academic/legal/news lookups (Wikipedia, Wikidata, Crossref, OpenAlex, OpenCorporates, SEC EDGAR, GDELT news), stealthy web scraping/extraction, SearXNG search, library docs lookup, or live sports scores/standings (Sofascore). Broader than ask_web_search — use for structured or academic sources. Do NOT use for weather — its multi-round scrape chain measured 30-228s live for weather lookups vs a few seconds via ask_web_search; use ask_web_search for any weather/forecast question instead.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The research question or lookup (e.g., 'find the SEC filing for...', 'who is playing tonight in the Premier League').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_VISION_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_vision_agent",
        "description": "Invoke the vision subagent to take a screenshot, list windows, run OCR on an image, parse a document, update form fields visually, or search via Spotlight.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Instructions for vision actions (e.g., 'take a screenshot and read the text on screen').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_UTILITY_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_utility_agent",
        "description": "Invoke the utility subagent for miscellaneous actions: system health status, geocoding an address, generating a local image, or analyzing/organizing a cluttered directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Instructions for utility actions (e.g., 'check system status', 'geocode 1600 Amphitheatre Parkway').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_TRANSPORT_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_transport_agent",
        "description": "Invoke the transport subagent for real bus/transit ETAs (bus_eta) and Uber ride estimates/trip history (estimate_uber_ride, get_my_uber_trips). Use this — not ask_web_search — for any travel-time/ETA/directions/transit/Uber question; it hits live transit data or your real logged-in Chrome instead of a general web search, and must always be called fresh (never estimate from a prior answer in this conversation, even for a similar-looking follow-up).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The transport question, fully self-contained (e.g., 'bus ETA from Camden St & Broadway, Everett to Boston Medical Center', 'estimate an Uber ride to the airport').",
                }
            },
            "required": ["query"],
        },
    },
}

ASK_YOUTUBE_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_youtube_agent",
        "description": "Search YouTube for videos by keyword/topic/channel, or get the transcript of a YouTube video (falls back to local whisper transcription when no captions exist). Use this — not ask_web_search — for any YouTube search or transcript request.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The YouTube request, fully self-contained (e.g., 'search youtube for Robin Lebrun productivity videos', 'get the transcript of https://www.youtube.com/watch?v=abc123').",
                }
            },
            "required": ["query"],
        },
    },
}


ASK_SEND_MESSAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_send_message",
        "description": "Send an async message to another agent. The target agent processes it on its next poll cycle. Agents: orchestrator (main), dev (code), research (deep research), email (gmail), automation (workflows), science_scout (papers), reddit_intel (reddit).",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Target agent id"},
                "subject": {"type": "string", "description": "Brief subject line"},
                "body": {"type": "string", "description": "Full message body"},
                "priority": {"type": "integer", "description": "0=normal, 1=important, 2=urgent", "default": 0},
            },
            "required": ["to", "subject", "body"],
        },
    },
}


ASK_FETCH_URL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_fetch_url",
        "description": (
            "Fetch a specific URL directly and return its readable text content. "
            "Fast, no subagent LLM round-trip — use for a URL the user gave you or "
            "one found in prior results (e.g. a search hit, an email link). "
            "Not for open-ended search (use ask_web_search) or pages needing login/"
            "clicking/forms (use ask_browser_agent)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch (include https:// if not present).",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Max characters of page text to return. Default 3000.",
                    "default": 3000,
                },
            },
            "required": ["url"],
        },
    },
}


# ─── Executors ────────────────────────────────────────────────────────────────

def _get_chat_id():
    from tools.registry import CURRENT_CHAT_ID
    return CURRENT_CHAT_ID.get()

_SKIP_EXTRACTION_RE = re.compile(
    r"^(https?://|site:|@|#|tell me about |how (?:many|much|long|old|far|big|tall)|"
    r"what is the (?:price|cost|score|date|time) |(?:calculate|compute|convert|translate) )",
    re.IGNORECASE,
)

_EXTRACTION_PROMPT = (
    "You are a search-query extractor. Given a user message and conversation "
    "context, extract ONLY the search query that needs to be looked up. Strip "
    "out action words (call me, send me, tell me, show me, find, look up, check, "
    "search for, etc.) and resolve pronouns/context from the conversation summary. "
    "Return ONLY the clean search query string, nothing else."
)


def _extract_search_query(raw_query: str) -> str:
    if len(raw_query) < 15 or len(raw_query.split()) <= 3:
        return raw_query
    if _SKIP_EXTRACTION_RE.search(raw_query):
        return raw_query
    chat_id = _get_chat_id()
    if chat_id is None:
        return raw_query
    try:
        from store.conversation import _summary_cache, _summary_lock
        with _summary_lock:
            cached = _summary_cache.get(str(chat_id))
            summary_text = cached.get("summary", "") if cached else ""
        if not summary_text:
            return raw_query
        from clients.cloud_client import raw_completion
        from clients import cloud_client
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
        prompt = (
            f"User message: {raw_query}\n\n"
            f"Conversation context (summary): {summary_text}\n\n"
            "Extract the search query from the user's message, using the "
            "conversation context to resolve what the user is referring to."
        )
        def _call_extract():
            choice = raw_completion(
                model=cloud_client.CLOUD_FALLBACK_MODEL,
                messages=[
                    {"role": "system", "content": _EXTRACTION_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                tools=[],
                bypass_budget=True,
            )
            return (choice.message.content or "").strip()
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            extracted = pool.submit(_call_extract).result(timeout=8)
        except _FutTimeout:
            _log.debug("[query-extract] timed out after 8s for %r", raw_query[:80])
            return raw_query
        finally:
            pool.shutdown(wait=False)
        extracted = extracted.strip('"').strip("'")
        if extracted and 3 <= len(extracted) <= 500:
            _log.info("[query-extract] chat_id=%s raw=%r -> extracted=%r", chat_id, raw_query[:80], extracted)
            return extracted
    except Exception:
        _log.debug("[query-extract] extraction failed for %r, using raw", raw_query[:80], exc_info=True)
    return raw_query


import collections
import logging
import re

_log = logging.getLogger(__name__)

# ponytail: mechanical backstop for the orchestrator re-searching the same
# entity across multiple rounds (observed 2026-07-11: prompt-only rule was
# not enough — a 3-entity comparison re-issued the same 3 ask_web_search
# calls in rounds 1-4, 14 calls/69s total). Keyed by (parent orchestrator
# run_id, intent, normalized query) so only exact-repeat calls within the
# SAME request are short-circuited — a genuinely new question, or the same
# question in a different request, still runs normally. Bounded by run
# count (not time) since run_ids aren't cleaned up otherwise; upgrade to a
# TTL if a run ever needs cache invalidation mid-flight.
_SUBAGENT_CACHE: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
_SUBAGENT_CACHE_MAX_RUNS = 200


def _cache_key(query: str) -> str:
    return " ".join(query.lower().split())


# 2026-07-11: harness.py's ORCHESTRATOR_SYSTEM_PROMPT already tells the model
# "call ask_web_search AT MOST ONCE PER ENTITY, EVER" — but that's prompt-only,
# and the exact-text dedupe above only catches literal repeat queries. Confirmed
# live: a "don't leave anything out" voice query re-searched the same two
# entities (geothermal, hydro) across 5 rounds with differently-worded queries
# per attribute (cost, heat-pump-vs-electric, flow rate, cost-per-watt...) —
# each string was "new" to the exact-text cache, so nothing stopped it; 12+
# searches, 50s+ before a single token of the answer streamed. This is a
# code-level backstop for that specific gap: near-duplicate detection by
# significant-word overlap (the entity noun persists across attribute-varied
# rephrasings; generic words don't), scoped to intent="temporal" only
# (ask_web_search) — other subagents don't have this fan-out failure mode.
_ENTITY_CACHE: "collections.OrderedDict[str, list]" = collections.OrderedDict()
_ENTITY_CACHE_MAX_RUNS = 200
# Overlap coefficient (|A∩B| / min(|A|,|B|)), not Jaccard — these queries carry
# 1-2 stable entity words plus several attribute words that vary per call
# ("cost", "heat pump", "flow rate", "cost per watt"...), so whole-query Jaccard
# stays under 0.3 for genuine re-searches of the same entity (measured live:
# 0.22-0.33) while overlap coefficient correctly reads 0.4-0.5 for the same
# cases, since it's normalized against the smaller/more-diluted set instead of
# the combined union.
_ENTITY_OVERLAP_THRESHOLD = 0.4
_ENTITY_STOPWORDS = frozenset("""
    the a an of for and or vs versus in on to at is are with without by from
    home system systems cost costs price pricing per typical residential
    installed installation comparison compare guide complete full detailed
    rundown pros cons how much what does 2024 2025 2026 2027
""".split())


def _significant_tokens(query: str) -> frozenset:
    words = re.findall(r"[a-z0-9]+", query.lower())
    return frozenset(w for w in words if w not in _ENTITY_STOPWORDS and len(w) > 2)


def _near_duplicate_entity_query(parent_run_id: str, query: str) -> bool:
    tokens = _significant_tokens(query)
    if len(tokens) < 2:
        return False
    seen = _ENTITY_CACHE.get(parent_run_id) or []
    for prior in seen:
        smaller = min(len(tokens), len(prior))
        if not smaller:
            continue
        overlap = len(tokens & prior) / smaller
        if overlap >= _ENTITY_OVERLAP_THRESHOLD:
            return True
    return False


def _record_entity_query(parent_run_id: str, query: str) -> None:
    tokens = _significant_tokens(query)
    if len(tokens) < 2:
        return
    seen = _ENTITY_CACHE.setdefault(parent_run_id, [])
    if len(seen) >= 50:
        seen.pop(0)
    seen.append(tokens)
    _ENTITY_CACHE.move_to_end(parent_run_id)
    while len(_ENTITY_CACHE) > _ENTITY_CACHE_MAX_RUNS:
        _ENTITY_CACHE.popitem(last=False)


def _run_subagent(intent: str, query: str) -> str:
    """Run an intent-pipeline subagent and append a machine-readable footer.

    The footer tells the orchestrator WHAT the subagent actually did (which tools
    ran, how many errored) — without it, "I found nothing" and "I never looked"
    produce identical prose, which caused confident false negatives (a subagent
    that skipped the right tool reported absence as fact). The orchestrator's
    system prompt requires status=ok coverage before absence claims.
    """
    import uuid

    import harness
    from log_config import CURRENT_RUN_ID
    from store import trace_store

    parent_run_id = CURRENT_RUN_ID.get()
    qkey = (intent, _cache_key(query))
    if parent_run_id:
        run_cache = _SUBAGENT_CACHE.get(parent_run_id)
        if run_cache is not None and qkey in run_cache:
            return run_cache[qkey] + "\n\n[dedupe: identical subagent call already made earlier this turn — result reused, not re-run]"
        if intent == "temporal" and _near_duplicate_entity_query(parent_run_id, query):
            _log.info("[entity-dedupe] run_id=%s blocked near-duplicate ask_web_search query=%r", parent_run_id, query)
            return (
                "[skipped: an earlier ask_web_search call this turn already covered this same "
                "topic/entity from a different angle — re-searching it again is not allowed. "
                "Synthesize your answer from the ask_web_search results you already have, or "
                "note the gap; do not call ask_web_search again for this entity.]"
            )

    rid = uuid.uuid4().hex[:8]
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTO
    _pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = _pool.submit(harness._execute_intent_pipeline, intent=intent, query=query, run_id=rid)
        res = fut.result(timeout=120)
    except _FutureTO:
        res = f"[subagent timeout] {intent} subagent did not return within 120s"
    finally:
        _pool.shutdown(wait=False)
    try:
        summary = trace_store.summarize_run(rid)
    except Exception:
        summary = {"status": "degraded", "tools": [], "errors": 0}
    footer = (
        f"[subagent intent={intent} status={summary['status']} "
        f"tools={','.join(summary['tools']) or 'none'} errors={summary['errors']}]"
    )
    if intent in ("temporal", "research_connectors", "youtube", "x_search"):
        chat_id = _get_chat_id()
        if chat_id is not None:
            try:
                from store.conversation import _summary_cache, _summary_lock
                with _summary_lock:
                    cached = _summary_cache.get(str(chat_id))
                    summary_text = cached.get("summary", "") if cached else ""
                if summary_text:
                    summary_words = set(re.findall(r"[a-z0-9]{4,}", summary_text.lower()))
                    result_words = set(re.findall(r"[a-z0-9]{4,}", res[:1000].lower()))
                    if summary_words and len(summary_words) >= 3 and len(result_words) >= 3:
                        overlap = len(summary_words & result_words)
                        if overlap == 0:
                            _log.warning(
                                "[relevance-guard] parent_run=%s query=%r summary_keys=%s result_keys=%s",
                                parent_run_id or "?", query[:80], sorted(summary_words)[:10], sorted(result_words)[:10],
                            )
                            result = (
                                "[OFF-TOPIC WARNING: this search result has zero keyword overlap "
                                "with the conversation context. It may be about a completely "
                                "different topic than what the user intended. Verify with the "
                                "user before presenting as fact.]\n\n" + result
                            )
            except Exception:
                pass

    result = f"{res}\n\n{footer}"

    if parent_run_id:
        run_cache = _SUBAGENT_CACHE.setdefault(parent_run_id, {})
        run_cache[qkey] = result
        _SUBAGENT_CACHE.move_to_end(parent_run_id)
        while len(_SUBAGENT_CACHE) > _SUBAGENT_CACHE_MAX_RUNS:
            _SUBAGENT_CACHE.popitem(last=False)
        if intent == "temporal":
            _record_entity_query(parent_run_id, query)

    return result


def exec_ask_local_agent(args: dict) -> str:
    return _run_subagent(
        intent="filesystem",
        query=args["query"],
    )


def _resolve_orchestrator_path(path: str) -> str:
    """Expand ~ and normalize a path against the real home dir.

    2026-07-17: these direct file-op executors previously passed `path`
    straight to cat/open/os.remove with no normalization, relying entirely on
    the system prompt telling the model to pre-resolve paths itself — the
    same class of gap already fixed for form/PDF paths (local_agent_nodes.py)
    and gmail attachments (gmail.py:_resolve_attachment_path). LLMs
    frequently emit a literal "~" the shell would expand but Python won't.
    """
    import os
    return os.path.expanduser(path)


def exec_ask_read_file(args: dict) -> str:
    """Read a file directly — no LLM overhead for simple reads."""
    import subprocess, shlex
    path = _resolve_orchestrator_path(args["path"])
    try:
        result = subprocess.run(
            ["cat", path], capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return f"[error] {result.stderr.strip() or 'could not read file'}"
        content = result.stdout
        if content and not content.isprintable() and "\n" not in content[:100]:
            return "[error] binary file — use ask_vision_agent for OCR or ask_run_command with a suitable tool"
        if len(content) > 8000:
            content = content[:7800] + f"\n... [truncated — {len(result.stdout)} chars total]"
        return content or "[empty file]"
    except Exception as e:
        return f"[error] {e}"


def exec_ask_fetch_url(args: dict) -> str:
    """Fetch a URL directly — no LLM overhead, delegates to tools.fetch_url."""
    from tools.fetch_url import execute as _fetch_url
    return _fetch_url(args["url"], max_chars=args.get("max_chars", 3000))


def exec_ask_write_file(args: dict) -> str:
    """Write content to a file — no LLM overhead."""
    import os
    path = _resolve_orchestrator_path(args["path"])
    content = args["content"]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"[error] {e}"


def exec_ask_delete_file(args: dict) -> str:
    """Delete a file or empty directory directly."""
    import os, shutil
    path = _resolve_orchestrator_path(args["path"])
    try:
        if os.path.islink(path):
            os.remove(path)
            return f"Deleted symlink: {path}"
        if os.path.isdir(path):
            shutil.rmtree(path)
            return f"Deleted directory: {path}"
        elif os.path.exists(path):
            os.remove(path)
            return f"Deleted file: {path}"
        else:
            return f"[error] Path not found: {path}"
    except Exception as e:
        return f"[error] {e}"


def exec_ask_rename_file(args: dict) -> str:
    """Rename/move a file or directory directly."""
    import os, shutil
    src = _resolve_orchestrator_path(args["source"])
    dst = _resolve_orchestrator_path(args["destination"])
    try:
        if not os.path.exists(src):
            return f"[error] Source not found: {src}"
        shutil.move(src, dst)
        return f"Moved/renamed: {src} → {dst}"
    except Exception as e:
        return f"[error] {e}"


def exec_ask_run_command(args: dict) -> str:
    """Run a bash command and return output."""
    import subprocess
    cmd = args["command"]
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        combined = out
        if err:
            combined = (combined + "\n[stderr] " + err).strip()
        if len(combined) > 6000:
            combined = combined[:5800] + f"\n... [truncated — {len(combined)} total chars]"
        return combined or "(no output)"
    except subprocess.TimeoutExpired:
        return "[error] Command timed out after 60s"
    except Exception as e:
        return f"[error] {e}"


def exec_ask_web_search(args: dict) -> str:
    query = _extract_search_query(args["query"])
    return _run_subagent(
        intent="temporal",
        query=query,
    )


def exec_ask_browser_agent(args: dict) -> str:
    return _run_subagent(
        intent="browser",
        query=args["query"],
    )


def exec_ask_macos_native(args: dict) -> str:
    return _run_subagent(
        intent="macos_native",
        query=args["query"],
    )


def exec_ask_email_agent(args: dict) -> str:
    return _run_subagent(
        intent="email",
        query=args["query"],
    )


def exec_ask_tasks_agent(args: dict) -> str:
    return _run_subagent(
        intent="tasks",
        query=args["query"],
    )


def exec_ask_calendar_agent(args: dict) -> str:
    return _run_subagent(
        intent="calendar",
        query=args["query"],
    )


def exec_ask_docs_agent(args: dict) -> str:
    return _run_subagent(
        intent="docs",
        query=args["query"],
    )


def exec_ask_sheets_agent(args: dict) -> str:
    return _run_subagent(
        intent="sheets",
        query=args["query"],
    )


def exec_ask_automation_agent(args: dict) -> str:
    return _run_subagent(
        intent="automation",
        query=args["query"],
    )


def exec_ask_dev_agent(args: dict) -> str:
    return _run_subagent(
        intent="dev_tools",
        query=args["query"],
    )


def exec_ask_messaging_agent(args: dict) -> str:
    return _run_subagent(
        intent="messaging",
        query=args["query"],
    )


def exec_ask_research_agent(args: dict) -> str:
    query = _extract_search_query(args["query"])
    return _run_subagent(
        intent="research_connectors",
        query=query,
    )


def exec_ask_vision_agent(args: dict) -> str:
    return _run_subagent(
        intent="vision",
        query=args["query"],
    )


def exec_ask_utility_agent(args: dict) -> str:
    return _run_subagent(
        intent="utility",
        query=args["query"],
    )


def exec_ask_transport_agent(args: dict) -> str:
    return _run_subagent(
        intent="transit",
        query=args["query"],
    )


def exec_ask_youtube_agent(args: dict) -> str:
    query = _extract_search_query(args["query"])
    return _run_subagent(
        intent="youtube",
        query=query,
    )

def exec_ask_x_agent(args: dict) -> str:
    query = _extract_search_query(args["query"])
    return _run_subagent(
        intent="x_search",
        query=query,
    )



def exec_ask_reddit_agent(args: dict) -> str:
    """Direct DB query, no LLM tool-loop — reads the reddit_intel insight store
    the background scan pipeline (jobs/handlers/reddit_intel.py) maintains.
    Hybrid score per the original design: 0.4 semantic + 0.25 recency +
    0.2 engagement(evidence_count) + 0.15 novelty(inverse evidence_count).
    """
    import math
    import time as _time
    from datetime import datetime, timezone

    from store import reddit_intel_store as _store

    query = args["query"]
    insights = _store.list_active_insights(limit=200)
    if not insights:
        return "[subagent intent=reddit status=degraded tools=none errors=0]\nNo Reddit insights collected yet."

    try:
        from jobs.handlers.reddit_intel import _get_knowledge_base
        kb = _get_knowledge_base()
        hits = {h["query"]: h.get("_distance", 2.0) for h in kb.search(query, top_k=50, source_filter="reddit_insight")}
    except Exception:
        hits = {}

    now = datetime.now(timezone.utc)
    scored = []
    max_evidence = max((i["evidence_count"] for i in insights), default=1)
    for ins in insights:
        distance = hits.get(ins["id"], 2.0)
        semantic = max(0.0, 1.0 - distance / 2.0)
        try:
            age_days = max((now - datetime.fromisoformat(ins["last_seen"])).days, 0)
        except (ValueError, TypeError):
            age_days = 30
        recency = math.exp(-age_days / 14.0)
        engagement = min(ins["evidence_count"] / max_evidence, 1.0)
        novelty = 1.0 / (1 + ins["evidence_count"])
        score = 0.40 * semantic + 0.25 * recency + 0.20 * engagement + 0.15 * novelty
        scored.append((score, ins))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:10]
    lines = [
        f"- {ins['summary']} (evidence={ins['evidence_count']}, wtp={ins['willingness_to_pay']:.2f}, "
        f"severity={ins['severity']:.2f}, subs={ins['subreddits_json']})"
        for _, ins in top
    ]
    body = "\n".join(lines)
    footer = f"[subagent intent=reddit status=ok tools=reddit_intel_store errors=0]"
    return f"{body}\n\n{footer}"


def exec_ask_science_scout_agent(args: dict) -> str:
    """Direct DB query, no LLM tool-loop — reads the science_scout claim store
    the background scan pipeline (jobs/handlers/science_scout.py) maintains.
    Same hybrid-score shape as exec_ask_reddit_agent (semantic/recency/
    engagement/novelty), evidence_count standing in for engagement.
    """
    import math
    from datetime import datetime, timezone

    from store import science_scout_store as _store

    query = args["query"]
    claims = _store.list_active_claims(limit=200)
    if not claims:
        return "[subagent intent=science_scout status=degraded tools=none errors=0]\nNo science_scout claims collected yet."

    try:
        from jobs.handlers.science_scout import _get_knowledge_base
        kb = _get_knowledge_base()
        hits = {h["query"]: h.get("_distance", 2.0) for h in kb.search(query, top_k=50, source_filter="science_claim")}
    except Exception:
        hits = {}

    now = datetime.now(timezone.utc)
    scored = []
    max_evidence = max((c["evidence_count"] for c in claims), default=1)
    for claim in claims:
        distance = hits.get(claim["id"], 2.0)
        semantic = max(0.0, 1.0 - distance / 2.0)
        try:
            age_days = max((now - datetime.fromisoformat(claim["last_seen"])).days, 0)
        except (ValueError, TypeError):
            age_days = 30
        recency = math.exp(-age_days / 14.0)
        engagement = min(claim["evidence_count"] / max_evidence, 1.0)
        novelty = 1.0 / (1 + claim["evidence_count"])
        score = 0.40 * semantic + 0.25 * recency + 0.20 * engagement + 0.15 * novelty
        scored.append((score, claim))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:10]
    lines = [
        f"- {c['summary']} (evidence_level={c['evidence_level']}, corroborations={c['evidence_count']}, "
        f"niches={c['niches_json']})"
        for _, c in top
    ]
    body = "\n".join(lines)
    footer = "[subagent intent=science_scout status=ok tools=science_scout_store errors=0]"
    return f"{body}\n\n{footer}"


QUERY_RECENT_TRACES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_recent_traces",
        "description": (
            "Look up what tools other subagent runs actually called, in past chat "
            "turns (this conversation or others) — not just the current turn's own "
            "footer. Use to check 'did we already look that up', debug a subagent "
            "that errored, or see what a specific tool has been used for recently."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "description": "Filter to runs that called this exact tool name, e.g. 'ask_email_agent'."},
                "has_error": {"type": "boolean", "description": "True = only runs with at least one erroring tool call."},
                "limit": {"type": "integer", "description": "Max runs to return, default 10."},
            },
            "required": [],
        },
    },
}


def exec_query_recent_traces(args: dict) -> str:
    from store import trace_store

    runs = trace_store.query_traces(
        tool=args.get("tool"),
        has_error=args.get("has_error"),
        limit=args.get("limit", 10),
    )
    if not runs:
        return "No matching trace runs found."

    lines = []
    for run in runs:
        lines.append(f"run={run['run_id']}")
        for e in run["entries"]:
            tool = e.get("tool", "?")
            err = " ERROR" if e.get("error") else ""
            summary = (e.get("result_summary") or "")[:150]
            lines.append(f"  - {tool}{err} ({e.get('elapsed_ms', 0)}ms): {summary}")
    return "\n".join(lines)


def exec_ask_deep_research(args: dict) -> str:
    from tools.deep_research import execute as deep_research_execute
    return deep_research_execute(
        query=args["query"],
        depth=args.get("depth", 2),
        breadth=args.get("breadth", 3),
        chat_id=_get_chat_id(),
        time_budget_seconds=args.get("time_budget_seconds", 270),
    )


def exec_ask_send_message(args: dict) -> str:
    from jobs.agent_message_queue import send_message as _save_msg
    from jobs import job_queue as _jq

    msg_id = _save_msg(
        from_agent=_AGENT_ID.get(),
        to_agent=args["to"],
        subject=args["subject"],
        body=args["body"],
        priority=args.get("priority", 0),
    )
    _jq.enqueue("agent_message", {"message_id": msg_id})
    return f"[message sent] to={args['to']} subject={args['subject']!r} id={msg_id[:8]}"
