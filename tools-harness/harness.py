"""
Main entrypoint for the local LLM tools harness.

Usage:
    from harness import run
    result = run("What's the latest on renewable energy?")
    result = run("What does useEffect do in React?", tools=["web_search"])
"""

import datetime
import os
import logging
import re
import threading
import uuid
from pathlib import Path
from dotenv import load_dotenv

_log = logging.getLogger(__name__)

# Telegram + WhatsApp share one process (messaging_supervisor.sh) and both funnel
# through run() below. Two bots firing near-simultaneously stack concurrent Ollama
# generations on one GPU -> evicts the warm set / times out, same failure mode the
# web_search-tool semaphore in ollama_client.py guards against. Cap concurrent
# messaging-originated run() calls; web UI callers are unaffected (they call run()
# directly, not run_for_messaging()).
# ponytail: bare semaphore, no queue/broker — one process, in-memory is enough.
_MESSAGING_GATE = threading.Semaphore(2)  # matches OLLAMA_MAX_LOADED_MODELS=2

load_dotenv(Path(__file__).parent / ".env")

from tools.registry import ALL_TOOLS, PLAN_TOOLS, tools_with_tags, CURRENT_CHAT_ID
from clients import ollama_client, cloud_client
from clients.router import classify_ide, classify_message, model_for_intent, reasoning_effort_for_intent, _SPORTS_RE

# opencode-model switch: route "make opencode use <nemotron/model>" straight to the
# deterministic config tool instead of the automation-agent loop (which used to hang).
# Match requires an opencode/model context AND a nemotron/tron token — so a bare
# "refactor the opencode tool" (no nemotron) or "what model are you" (no nemotron)
# still routes normally, but "make opencode use nemotron" (no literal "model") is caught.
_OPENCODE_MODEL_RE = re.compile(r"opencode|\bmodel\b", re.I)
_OPENCODE_MODEL_TOKEN_RE = re.compile(r"nemotron|nemo[-\s]?tron|\btron\b|deepseek", re.I)


MAX_ROUNDS = ollama_client.MAX_ROUNDS

def local_chat(**kwargs):
    """Dispatch to the cloud (OpenRouter/DeepSeek) or local (Ollama) chat client
    based on the model string — a cloud-prefixed model routes to cloud_client,
    everything else stays on ollama_client. Both expose the same chat(**kwargs)
    shape.

    Cloud stays the model even past the daily token budget (clients.cost_guard) —
    the budget is advisory/logged only, not a hard cutover to local gemma4, which
    is unreliable past a few chained tool calls.

    If cloud is unreachable at all (no network, missing API key, provider outage —
    cloud_client.chat() already retried its own cross-provider fallback and still
    failed), fall back to local ollama_client with DEFAULT_MODEL so the app keeps
    working offline instead of raising."""
    model = kwargs.get("model", ollama_client.DEFAULT_MODEL)
    if cloud_client.is_cloud_model(model):
        try:
            return cloud_client.chat(**kwargs)
        except cloud_client.BudgetExceededError as e:
            _log.warning("[local_chat] %s, continuing on cloud anyway", e)
            return cloud_client.chat(**kwargs, bypass_budget=True)
        except Exception:
            _log.warning("[local_chat] cloud failed, falling back to local", exc_info=True)
            kwargs["model"] = ollama_client.DEFAULT_MODEL
            return ollama_client.chat(**kwargs)
    return ollama_client.chat(**kwargs)
from tools.websearch import search as _run_websearch
from tools.query_guard import check_all as _guard_check, _WEATHER_REPLY
from agents.specialists.dispatch import dispatch as _specialist_dispatch
from agents.local_agent_graph import run_local_agent

# Code-task detection: regex patterns that indicate a code-related query
# so the local agent gets a leaner toolset + coding workflow instructions.
_CODE_TASK_PATTERNS = re.compile(
    r"\b(code|function|class|refactor|implement|write\s+(a\s+)?(function|class|test|script)|"
    r"add\s+(feature|method|endpoint|route|handler)|"
    r"fix\s+(the\s+)?(bug|issue|error|problem)|"
    r"debug|refactor|rewrite|migrate|"
    r"grep|search.*code|find.*function|where.*defined|"
    r"import|export|module|package|"
    r"git\s+(status|diff|log|add|commit|push|pull)|"
    r"parse|analyze.*code|"
    r"rename\s+(file|function|class|variable)|"
    r"move\s+file|copy\s+file)\b",
    re.I,
)

_DOCUMENT_TASK_PATTERNS = re.compile(
    r"\b(pdf|docx?|\.pdf|\.docx?|markdown|\.md|"
    r"fill(?:_|\s+)(the\s+)?form|form\s+field|detect_form_fields|"
    r"email\s+attachment|attach(?:ment|\s+as)|"
    r"convert.*(to\s+pdf|to\s+doc)|export.*(to\s+pdf|to\s+doc))\b",
    re.I,
)

def _infer_local_agent_task(query: str) -> str:
    """Determine task type for the local agent based on query content."""
    if _DOCUMENT_TASK_PATTERNS.search(query):
        return "document"
    if _CODE_TASK_PATTERNS.search(query):
        return "code"
    return "full"

from store.conversation import (
    get as conv_get,
    append as conv_append,
    trim_to_budget,
    compact_old_turns,
    get_lock,
    KEEP_RECENT_TURNS,
)

# Voice keeps a shorter verbatim history window than text chats — fewer prompt
# tokens per round shaves first-token latency on the most latency-sensitive
# channel. Older context still travels via the rolling summary.
_VOICE_KEEP_RECENT = 6

# 2026-07-11: code-level backstop for the VOICE OUTPUT MODE prompt above — a
# prompt-only "under 60 words" rule was tested live and ignored by the model
# for broad "pros and cons"/"rundown" queries (still got a 1913-char, ~300-word
# answer). Same class of gap as the multi-entity search-fanout rule earlier
# this session, which also needed a code backstop. This regex detects an
# explicit ask for detail; absent that, _voice_trim_reply() hard-caps length.
_VOICE_DETAIL_REQUESTED_RE = re.compile(
    r"\b(full|detailed?|complete|in[- ]depth|everything|don'?t leave "
    r"(?:anything|nothing) out|go deep|more detail|elaborate|thorough)\b",
    re.IGNORECASE,
)
_VOICE_MAX_WORDS = 70


def _voice_trim_reply(text: str, query: str) -> str:
    """Hard-cap a brabble_voice reply to ~_VOICE_MAX_WORDS unless the user's
    query explicitly asked for a detailed answer. Trims at a sentence
    boundary and appends a spoken offer for more, so a model that ignores the
    prompt-only length rule still produces a short, TTS-friendly reply."""
    if _VOICE_DETAIL_REQUESTED_RE.search(query):
        return text
    words = text.split()
    if len(words) <= _VOICE_MAX_WORDS:
        return text
    budget = " ".join(words[:_VOICE_MAX_WORDS])
    last_boundary = max(budget.rfind(". "), budget.rfind("! "), budget.rfind("? "))
    if last_boundary > len(budget) * 0.3:
        budget = budget[: last_boundary + 1]
    else:
        budget = budget.rstrip(",;: ") + "."
    return budget + " Want more detail?"
# Voice can't wait for a deep multi-round research grind (live-observed 8.7s on a
# rate-limited sports API falling back to multi-site scraping over 10 model rounds).
# Cap subagent rounds for voice-originated runs — a faster, possibly-shallower answer
# beats 9s of dead air. ponytail: raise if voice users start hitting truncated answers.
_VOICE_MAX_ROUNDS = 6
from tools.memory_tools import recall_block as memory_recall
from store.event_log import recent_activity_block

# Explicit memory-write phrasing — enables remember/forget in otherwise-toolless casual chat.
_MEMORY_TRIGGER_RE = re.compile(
    r"\b(remember|keep in mind|don'?t forget|make a note|note that|"
    r"forget (?:that|about|what)|no longer (?:true|the case))\b",
    re.I,
)

_HEALTH_EPI_DISEASE_RE = re.compile(
    r"\b(covid|covid-19|covid19|coronavirus|sars-cov-2|flu|influenza|mpox|monkeypox|ebola|dengue)\b",
    re.I,
)
_HEALTH_EPI_SIGNAL_RE = re.compile(
    r"\b(prevalence|incidence|cases?|deaths?|mortality|morbidity|statistics|"
    r"outbreak|epidemic|pandemic|spread|infection\s*rate|case\s*count|"
    r"hospitalization|vaccination\s*rate|active\s*cases?|surveillance)\b",
    re.I,
)
_VISUAL_REQUEST_RE = re.compile(
    r"\b(svg|diagram|flowchart|poster|banner|card|infographic|graphic|visual|"
    r"wireframe|mockup|logo|badge|timeline|roadmap)\b",
    re.I,
)
_VISUAL_ACTION_RE = re.compile(
    r"\b(create|make|design|draw|render|generate|show|build)\b",
    re.I,
)
_NESTED_SVG_FENCE_RE = re.compile(
    r"```\s*\n```svg\s*\n(?P<svg><svg[\s\S]*?</svg>)\s*\n```\s*\n```",
    re.I,
)
_DELETE_AUTOMATION_RE = re.compile(
    r"\b(delete|remove|wipe|purge)\b", re.I,
)


def _rewrite_weather_followup(query: str, prior_turns: list[dict]) -> str:
    """A bare answer to the weather guard's clarify question ("tokyo" answering
    "Which city's weather do you want?") carries no "weather" keyword, so
    neither the guard nor the classifier reliably recognize it from one word
    alone — the orchestrator then has to guess intent and picked
    ask_research_agent (6-round multi-engine scrape, 41s) instead of a quick
    ask_web_search (confirmed live 2026-07-10). Rewriting the query here lets
    the EXISTING forced-tool machinery (intent=="temporal", classified by
    classify_message() from the rewritten text) handle it correctly, instead
    of adding a second heuristic.
    """
    if (
        prior_turns
        and prior_turns[-1].get("role") == "assistant"
        and prior_turns[-1].get("content", "").strip() == _WEATHER_REPLY
        and not re.search(r"\bweather\b", query, re.I)
    ):
        return f"weather in {query.strip()}"
    return query

# Verify-on-absence / verify-on-undercoverage: a commitment-shaped question
# ("do I have X today/this week") is only trustworthy if the commitment
# sources were actually consulted. Originally only fired on "nothing found"
# phrasing (verify-on-absence); generalized 2026-07-28 to also fire when the
# query itself is commitment-shaped but <2 sources were called at all, even
# if the draft answer sounds confident — a wrong-but-confident answer from
# thin coverage is just as bad as a wrong "nothing found", and phrasing alone
# can't tell them apart. Still exactly one retry, straight-line, same nudge.
_ABSENCE_RE = re.compile(
    r"\b(no|nothing|not\s+any|don'?t\s+have|do\s+not\s+have|couldn'?t\s+find)\b"
    r".{0,40}\b(found|scheduled|due|planned|assignments?|appointments?|bookings?|"
    r"events?|tasks?|emails?|meetings?|today|tomorrow|upcoming)\b",
    re.I,
)
_COMMITMENT_QUESTION_RE = re.compile(
    r"\b(do\s+i\s+have|am\s+i\s+|is\s+there\s+anything|what'?s\s+(due|scheduled|planned)|"
    r"anything\s+(due|scheduled|planned|coming\s+up))\b.{0,60}\b(today|tomorrow|this\s+week|"
    r"this\s+month|upcoming|assignment|appointment|booking|meeting|deadline)\b",
    re.I,
)
# 2026-08-01: the regex above is a word-list ("do i have"/"anything due"/etc.) —
# a commitment question phrased outside it (confirmed live: "verify email and
# iMessage for any confirmed assignments we have coming with CCCS this month")
# skipped verify-on-absence and the claim-check entirely, with no fallback.
# `intent` at the call site is already LLM-classified (classify_message()), same
# fix pattern as the automation/temporal dispatch above — OR it in as a second,
# more semantic trigger rather than replacing the regex (which still catches
# cases correctly even off a stale/fallback-regex intent classification).
#
# Over-fire guard: `intent` alone is too broad — "email"/"messaging"/"imessage"
# fire on plain sends/searches too ("send an email to Bob" classifies intent=
# "email" but isn't a commitment question). Require intent-in-set AND a broad
# verification-cue hit in the query, not intent alone. The cue regex is
# deliberately looser than _COMMITMENT_QUESTION_RE's strict grammar shape
# (single words, no fixed phrase order) — `intent` already narrowed the
# semantic space, so the cue only needs to rule out plain commands.
# 2026-08-01: verified live against clients/router.py's classify_message() (the
# actual classifier harness.py's gate uses, not classify_telegram, a separate
# older path). "messaging" is never actually returned by the classifier
# (dead/harmless leftover). "whatsapp_search"/"slack"/"temporal" ARE real
# intents a commitment question can land on ("Check slack for confirmed
# meeting today" -> slack; "Is there anything coming up this week" -> temporal)
# and were missing — same blind-spot class as the original iMessage gap this
# fix targets. Safe to add "temporal" despite it being broad: the cue-word
# co-requirement (_COMMITMENT_CUE_RE) already rejects plain time queries like
# "what time is it right now" (verified: no cue-word match).
_COMMITMENT_INTENTS = frozenset({
    "calendar", "tasks", "reminder", "email", "imessage", "whatsapp_search", "slack", "temporal",
})
_COMMITMENT_CUE_RE = re.compile(
    r"\b(any|anything|verify|confirm(?:ed)?|check|find\s+out|due|scheduled|upcoming|"
    r"today|tomorrow|this\s+week|this\s+month)\b",
    re.I,
)
_COMMITMENT_SOURCES = frozenset({
    "ask_email_agent", "ask_calendar_agent", "ask_tasks_agent", "ask_messaging_agent",
})


def _is_commitment_shaped(text: str, intent: str) -> bool:
    return bool(
        _COMMITMENT_QUESTION_RE.search(text or "")
        or (intent in _COMMITMENT_INTENTS and _COMMITMENT_CUE_RE.search(text or ""))
    )


def _absence_unchecked_sources(answer: str, query: str, run_id: str, intent: str = "") -> set[str]:
    """Sources still unchecked for a commitment-shaped answer/question; empty set = fine."""
    if not (_ABSENCE_RE.search(answer or "") or _is_commitment_shaped(query, intent)):
        return set()
    from store import trace_store
    try:
        trace = trace_store.get_trace(run_id) or []
    except Exception:
        _log.debug("trace_store.get_trace failed, returning empty set", exc_info=True)
        return set()
    called = {t.get("tool") for t in trace} & _COMMITMENT_SOURCES
    # 2026-08-01: count-only >=2 threshold let the orchestrator satisfy
    # coverage by calling email+calendar+tasks while skipping messaging
    # entirely — reproduced live (CCCS query, 3 tools called, iMessage never
    # hit). iMessage/WhatsApp/Slack are first-class confirmation sources for
    # this user (see CLAUDE.md), not optional extras, so require it by name
    # whenever it's a plausible source, not just count.
    if "ask_messaging_agent" not in called:
        return _COMMITMENT_SOURCES - called
    return set() if len(called) >= 2 else _COMMITMENT_SOURCES - called


# Adversarial claim-check: generalizes verify-on-absence to confident-but-wrong
# answers. verify-on-absence only catches "nothing found" phrasing / thin
# coverage — a confident answer that misreads a real tool result (wrong date,
# wrong sender) sails through untouched. Gated on commitment-shaped queries
# with >=1 tool call only, so casual/no-tool turns never pay the extra
# LLM round. One cheap local model call, not cloud — this is a yes/no sanity
# check, not a reasoning task.
def _needs_claim_check(query: str, run_id: str, intent: str = "") -> list[dict]:
    """Trace entries if a claim-check should run against this answer, else []."""
    if not _is_commitment_shaped(query, intent):
        return []
    from store import trace_store
    try:
        trace = trace_store.get_trace(run_id) or []
    except Exception:
        _log.debug("trace_store.get_trace failed, returning empty list", exc_info=True)
        return []
    return [t for t in trace if t.get("tool")]


def _check_claim_against_trace(answer: str, trace: list[dict]) -> str | None:
    """Returns a discrepancy string if the answer states something the trace
    doesn't support, else None. Failures (model unreachable, bad output) are
    treated as VALID — this is a best-effort sanity net, not a hard gate."""
    evidence = "\n".join(
        f"- {t.get('tool')}({t.get('args')}) -> {t.get('result_summary', '')[:200]}"
        for t in trace[-8:]
    )
    prompt = (
        "Tool results from this turn:\n" + evidence +
        f"\n\nDraft answer: {answer}\n\n"
        "Does the draft answer follow directly from these tool results, or does it "
        "state a detail (date, name, amount, status) not present in them? "
        "Reply with exactly one line: either 'VALID' or 'INVALID: <what is wrong>'."
    )
    verdict = None
    try:
        from clients.ollama_client import chat as _local_chat_raw, DEFAULT_MODEL
        verdict = _local_chat_raw(user_message=prompt, model=DEFAULT_MODEL, options={"num_predict": 60})
    except Exception:
        # Local model unreachable/unloaded (e.g. gemma4:12b-mlx 404) — fall back
        # to cloud rather than silently no-op'ing the whole check.
        try:
            from clients.cloud_client import chat as _cloud_chat_raw
            verdict = _cloud_chat_raw(user_message=prompt, max_rounds=1, bypass_budget=True)
        except Exception:
            return None
    verdict = (verdict or "").strip()
    if verdict.upper().startswith("INVALID"):
        return verdict.split(":", 1)[1].strip() if ":" in verdict else verdict
    return None


ORCHESTRATOR_SYSTEM_PROMPT = """You are the clixen Orchestrator Agent, a highly capable coordinator. Your job is to help the user by deciding whether to answer their request directly or route it to one of your specialized subagents.

Always evaluate the user's query in the context of the conversation history.

Your subagent tools:
1. `ask_local_agent(query)`: Complex multi-step filesystem operations: find files, copy/move, parse/fill PDF/DOCX forms, run Python.
2. `ask_read_file(path)`: Read file contents at an absolute path. Fast, no LLM.
3. `ask_write_file(path, content)`: Create or overwrite a file with text content. Fast, no LLM.
4. `ask_delete_file(path)`: Delete a file or directory at an absolute path. Fast, no LLM.
5. `ask_rename_file(source, destination)`: Rename or move a file/folder. Fast, no LLM.
6. `ask_run_command(command)`: Run any bash command (ls, find, mv, grep, etc.) and return output.
7. `ask_web_search(query)`: Search the internet for recent events, schedules, prices, factual questions post-2024.
8. `ask_browser_agent(query)`: Browser automation (clicking, logging in, DoorDash, Uber, banking).
9. `ask_macos_native(query)`: Read/write clipboard, list Safari tabs, manage macOS Notes, run AppleScript.
10. `ask_email_agent(query)`: Check, read, summarize, or send emails.
11. `ask_tasks_agent(query)`: Create, complete, list, or delete tasks/reminders.
12. `ask_calendar_agent(query)`: Manage calendar events.
13. `ask_docs_agent(query)`: Create, read, list, or append to Google Docs.
14. `ask_sheets_agent(query)`: Create, read, list, append rows to, or update cells in Google Sheets.
{deep_research_line}
16. `ask_automation_agent(query)`: Create, list, pause, resume, delete, or trigger scheduled automations/workflows.
17. `ask_dev_agent(query)`: Git operations (status, diff, log, add, commit, checkout, worktree) or REPL/window inspection (kernel vars, reset kernel, list windows, screenshot).
18. `ask_messaging_agent(query)`: Search/send iMessage, search/send WhatsApp, search Slack, or set a reminder.
19. `ask_research_agent(query)`: Structured/academic lookups (Wikipedia, Wikidata, Crossref, OpenAlex, OpenCorporates, SEC EDGAR, GDELT news), stealthy web scraping, SearXNG search, library docs, or live sports scores/standings via the Sofascore connector (sofascore_live_scores, sofascore_get_event, sofascore_get_standings, sofascore_search_teams, etc.). Use this over ask_web_search when the source needs to be authoritative/structured (a filing, a paper, a standings table, a match score) rather than a general web answer.
20. `ask_vision_agent(query)`: Take a screenshot, list windows, run OCR, parse a document, update form fields visually, or search via Spotlight.
21. `ask_utility_agent(query)`: System health status, geocode an address, generate a local image, or analyze/organize a cluttered directory.
22. `ask_transport_agent(query)`: Real bus/transit ETAs, or Uber ride time/price estimates and trip history. Use this — not ask_web_search — for travel-time/ETA/directions/transit questions.
23. `ask_youtube_agent(query)`: Search YouTube for videos, or get the transcript of a YouTube video (auto-falls back to local whisper transcription when no captions exist). Use this — not ask_web_search — for YouTube search or transcript requests.
24. `ask_opencode(query)`: Delegate a coding task to the opencode agent (independent AI coding CLI with its own filesystem/git access). Use this — not ask_local_agent — for multi-file refactors, bug fixes that need investigation across files, writing tests, or any coding task substantial enough to benefit from a second agent working independently. Be specific: include file paths, expected behavior, error messages, constraints. For git status/diff/log or REPL/window inspection alone, use ask_dev_agent instead.
25. `ask_x_agent(query)`: Search X posts, read a specific X post, or retrieve an account's recent posts using the configured twscrape account.
26. `ask_fetch_url(url)`: Fetch a specific URL and return its readable text content. Use this to read the content behind links found in email results, search hits, or any URL the user mentions. Fast, no LLM overhead.

CRITICAL INSTRUCTIONS:
- User's home directory: {home_dir}. Resolve relative paths (~/Documents, /documents, downloads) to absolute paths under it before passing them to subagents.
- For SIMPLE file operations choose the direct tool: ask_read_file, ask_write_file, ask_delete_file, ask_rename_file, or ask_run_command.
- For COMPLEX multi-step or ambiguous operations (finding files, filling forms, searching) use ask_local_agent.
{deep_research_critical}
- `apply_organization` (via ask_utility_agent) can delete files if the query asks for cleanup — always confirm with the user which files/scheme before calling it destructively, same as ask_delete_file.
- If the user's query is a simple greeting, casual conversation, or doesn't require any actions/tools, answer them directly.
- If the user's query is context-dependent (e.g., "Same for infochir", "what about the other one", "delete it"), resolve the pronouns and reference the conversation history to construct a completely self-contained query for the subagent tool.
- FETCH BEFORE SEARCH: When the user references "the email", "the newsletter", "the article", or any past document that contains a URL in the conversation history, call ask_email_agent FIRST to get the email body and any links, then call ask_fetch_url to read that link's content, THEN use the full content to construct your search query for ask_web_search / ask_youtube_agent / ask_research_agent. Never search blind when a source URL is in context — read it first.
- OFF-TOPIC DETECTION: If a subagent result is prefixed with "[OFF-TOPIC WARNING:", do NOT present the result as fact. Instead, tell the user what you searched for and ask "Is that what you meant?" before proceeding. The warning means the search results have zero overlap with the conversation context, suggesting the subagent may have searched for the wrong topic.
- If a subagent tool failed earlier in this conversation — including with an auth/permission/access error — and the user asks again, call that subagent tool again. Do NOT assume from a past failure that the capability is permanently unavailable — subagents can self-recover (e.g. refreshed credentials) between turns, so always retry fresh.
- If the user's message explicitly asks for part of the answer in a specific language (e.g. "...and tell me in English what X is", "...réponds en français pour Y"), honor that per-clause language switch for that part of the answer even if the rest of the reply is in the query's dominant language.
- Always respond in the language of the user's most recent message — never let the language of prior turns in this conversation, your own prior replies, or the "What you remember about the user" memory block determine your reply's language. Those are inputs to read for facts, not a style to imitate (root-caused 2026-07-17: a plain English query on a brand-new chat, zero prior turns, still got a French reply — traced to `recall_block()` injecting genuinely French-language recalled memories, which the model then mirrored instead of matching the query language).
- Never write an automation's message/reply template (or describe an automation result) as doing more than its action_type actually does — that text reaches the user verbatim, so a false claim there is a fabricated status report, not a cosmetic one.
- When building an automation, if the request implies logic your tools can't actually express, or it's ambiguous whether a step should terminate the workflow, do NOT silently build a workaround — name the specific gap and your proposed workaround, then ask, before creating anything. This is a persistent automation that keeps firing on that logic every future run, so a wrong guess here is far costlier than in normal conversation.
- Always provide the most direct, clean, and helpful answer. Do not describe what you are about to do; just do it.
- HONESTY: If you don't know something, a tool returned nothing useful, or a subagent failed/timed out, say so plainly — "I don't know" or "I don't have that information" — instead of guessing or filling the gap with a plausible-sounding fabrication. State uncertainty when it exists; do not present a guess with the same confidence as a verified fact. This is a hard rule, not a style preference — it overrides the instruction above to "always provide the most direct answer" when directness would mean guessing.
- Never answer a request for data/facts/information with zero tool calls — if you're tempted to answer from memory alone, call the relevant subagent first (ask_local_agent for files/notes, ask_web_search/ask_research_agent for facts) and answer from what it returns.
- "local data"/"my data"/"data you have" defaults to the user's own files, notes, memory, or device state (ask_local_agent) — not geography ("near me", "local clinics"). Only read it as geographic if the query itself names a place or a location-bound service.
- PROACTIVE CALLS: You have a `call_my_phone(message)` tool. Call proactively whenever you find something interesting — not just urgent alerts. The user is a scientist, engineer, and doctor; call about novel research findings, clever engineering solutions, intriguing medical cases or papers, unexpected connections across fields, or anything you'd excitedly tell a curious colleague about. If you'd text it with "this is cool", it's worth a call. Trust your judgment — no fixed checklist. `call_my_phone` is a live interactive tool independent of the scheduled-automation system.
- DELIVER FULL SCOPE: Finish the whole task, not just the easy parts. If the user asks "search X and call me with the findings", complete BOTH steps — don't report the search results without calling. If part of the task is blocked, finish every other part first, then say what you left out and why.
- TRUST BUT VERIFY: After a subagent reports findings, verify the result before relaying to the user. Check that the subagent actually used the right tools (not just described them), that the answer matches the question asked, and that no obvious gaps exist. A subagent's summary describes what it INTENDED to do — not necessarily what it did.
- PARALLELISM: When tasks are independent, launch them concurrently in a single message. Research tasks (search, email check, calendar) can run in parallel. Write tasks (send, create, delete) should be sequential.
- PURPOSE-DRIVEN QUERIES: When calling subagents, include a brief purpose so they calibrate depth: "Find today's newsletter — this will inform a YouTube search" vs "Deep research on cardiac aging — I need a comprehensive report with citations." Don't just pass the raw user message; craft a query that tells the subagent what you need and why.
- SELF-VERIFY: Before declaring a task done, run one quick verification step: if you fetched a URL, check the content is relevant; if you searched YouTube, confirm results are about the right topic; if you called the user, confirm the call connected (not just "attempted"). If the verification fails, fix it before reporting completion.
- PLANNING: For any request needing 3+ tool calls, call update_task_plan with the ordered steps before starting, then mark_done as each finishes. Skip it for simple 1-2 tool-call requests — don't add ceremony where it isn't needed.
- REMEMBER PROACTIVELY: If a tool result surfaces a durable fact about the user (a preference, a recurring detail, a correction to something in the memory block above) that isn't already covered by "What you remember about the user", call remember() yourself — don't wait for the user to say "remember that". Only for facts that should persist across future conversations, not one-off task details.{fragments}"""

# --- Topic-scoped prompt fragments, injected conditionally per turn (see _select_orchestrator_fragments) ---
# Split out of the monolithic ORCHESTRATOR_SYSTEM_PROMPT above 2026-07-17 to stop it growing
# unboundedly on every future incident — see ~/.claude/plans/great-this-harness-system-cheerful-hellman.md.
from skills_data.orchestrator_fragments import (
    schedule_fanout as _frag_schedule_fanout,
    web_search_dispatch as _frag_web_search_dispatch,
    automation_dsl as _frag_automation_dsl,
    remember_action as _frag_remember_action,
)

_FRAGMENT_TRIGGERS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"remind|schedule|calendar|appointment|deadline|meeting|do i have", re.I), "schedule_fanout", _frag_schedule_fanout.FRAGMENT),
    (re.compile(r"search|look up|find out|what'?s the|latest|score|standings|price of|news|eta|bus|transit|uber", re.I), "web_search_dispatch", _frag_web_search_dispatch.FRAGMENT),
    (re.compile(r"automat|workflow|pipeline|trigger|watcher|branch|condition", re.I), "automation_dsl", _frag_automation_dsl.FRAGMENT),
    (re.compile(r"remember that|keep in mind|don'?t forget", re.I), "remember_action", _frag_remember_action.FRAGMENT),
]

# One-turn stickiness so a topic-less follow-up ("yes do that") still gets the
# fragment from the turn before it — not longer, or stale fragments bloat
# unrelated later turns in a long conversation.
_LAST_ORCH_FRAGMENTS: dict[str, list[str]] = {}


def _select_orchestrator_fragments(query: str, chat_id: str) -> str:
    matched = [(name, text) for pattern, name, text in _FRAGMENT_TRIGGERS if pattern.search(query or "")]
    if not matched:
        carried = _LAST_ORCH_FRAGMENTS.get(chat_id)
        if carried:
            matched = [(name, text) for pattern, name, text in _FRAGMENT_TRIGGERS if name in carried]
        _LAST_ORCH_FRAGMENTS[chat_id] = []
    else:
        _LAST_ORCH_FRAGMENTS[chat_id] = [name for name, _ in matched]
    if not matched:
        return ""
    return "\n" + "\n".join(text for _, text in matched)


def _execute_intent_pipeline(intent: str, query: str, chat_id: str | None = None, run_id: str = None) -> str:
    res, _, _ = run(
        query=query,
        intent=intent,
        chat_id=None,  # stateless subagent execution
        orchestrated=False,
        run_id=run_id,
    )
    return res


def _get_optimized_opts(intent: str, model: str) -> dict:
    """Return Ollama options optimized for the given intent.

    KV cache is pre-allocated at 16384 during warmup. Changing num_ctx
    between requests forces Ollama to re-allocate (5-13s penalty on
    gemma4:12b-mlx). Keep num_ctx consistent at 16384 for all intents
    to avoid this. `num_predict` is safe to vary — it only caps
    generation length, doesn't affect cache.
    """
    opts = {}
    if intent in ("classify", "router", "intent", "guard"):
        opts["num_predict"] = 20
    elif intent == "casual":
        opts["num_predict"] = 512  # match ollama_client default for thorough answers
    else:
        opts["num_predict"] = 2048
    return opts


def _should_force_health_websearch(query: str, intent: str) -> bool:
    """Route live epidemiology queries into web search even if chat routing misses."""
    if intent in {"temporal", "web_search"} or intent.startswith("temporal_"):
        return False
    return bool(_HEALTH_EPI_DISEASE_RE.search(query) and _HEALTH_EPI_SIGNAL_RE.search(query))


def _wants_inline_svg(query: str) -> bool:
    query = query.strip()
    if not query:
        return False
    return bool(_VISUAL_REQUEST_RE.search(query) and _VISUAL_ACTION_RE.search(query))


def _normalize_svg_reply(text: str) -> str:
    """Unwrap malformed nested fences around SVG replies."""
    if not text:
        return text
    match = _NESTED_SVG_FENCE_RE.search(text)
    if not match:
        return text
    fixed = f"```svg\n{match.group('svg').strip()}\n```"
    return _NESTED_SVG_FENCE_RE.sub(fixed, text, count=1)


from _harness_fs_actions import LocalFsAction, parse_local_fs_action, execute_local_fs_action  # noqa: F401
from _harness_dispatch_render import _render_dispatch_result  # noqa: F401

def run(*args, **kwargs):
    """Public entry: stamp CURRENT_RUN_ID correlation for the whole call, then
    delegate. try/finally guarantees the ContextVar resets on every exit
    (returns and raises) — no run_id leaks into subsequent sequential calls."""
    run_id = kwargs.get("run_id") or uuid.uuid4().hex[:8]
    from log_config import CURRENT_RUN_ID
    token = CURRENT_RUN_ID.set(run_id)
    try:
        return _run_impl(*args, **kwargs)
    finally:
        CURRENT_RUN_ID.reset(token)


def _run_impl(
    query: str,
    tools: list[str] = None,
    model: str = None,  # None = auto-route
    chat_id: str = None,  # for conversation history; None = stateless
    tts: bool = False,
    tts_voice: str = "af_heart",
    on_token=None,  # optional Callable[[str], None] for streaming tokens
    project_root: str = None,  # IDE: opened folder path for full-project awareness
    force_web_search: bool = False,  # UI web-search toggle: always inject web_search tool
    force_local_agent: bool = False,  # UI local-agent mode: force filesystem tools
    force_plan_mode: bool = False,  # UI Plan mode: read-only analysis
    images: list = None,  # base64-encoded image strings for vision models
    max_rounds: int = None,  # override intent-based default; None = use intent default
    round_timeout: float = None,  # per-round cloud API timeout (seconds), passed to local_chat
    manual_model_pick: bool = False,  # web UI "Models" dropdown: user chose this model by
    # hand, as opposed to it being an internal routing detail (Telegram/WhatsApp always pass
    # a concrete model string too, but that's classify_message()'s routing choice, not a
    # user override — must not trip the "strip IDE-only tools" guard below).
    intent: str = None,  # pre-computed by clients.router.classify_message(); when given,
    # skips internal classification entirely (fixes callers like Telegram/WhatsApp
    # re-deriving intent from the same text they already classified).
    specialist_hint: str = None,  # Classification.specialist_hint from the same call —
    # threaded into the filesystem specialist dispatch to skip its own regex classify().
    orchestrated: bool = True,
    run_id: str = None,  # caller-supplied trace id (subagent envelope / golden queries);
    # None = generate one. Lets orchestrator_tools read this run's trace_store entries.
    channel: str = "web",  # "web"/"telegram"/"whatsapp" — passed to classify_message()
    # when this call has to classify internally (intent is None). Callers that already
    # ran classify_message() themselves (Telegram/WhatsApp) pass intent= instead and
    # this is unused for them.
    extra_system_prompt: str = None,  # caller-supplied instructions appended to the real
    # system prompt (non-orchestrated path only — skills use this). Previously skills
    # smuggled their instructions into the user message instead; this carries them at
    # system-role weight like every other prompt block in this function.
) -> str:
    """
    Run a query through the local LLM.

    - model=None → router picks gemma4 based on intent
    - chat_id → enables per-chat sliding window context with per-chat locking
    """
    run_id = run_id or uuid.uuid4().hex[:8]
    model = model or None  # "" / falsy → auto-route (cloud primary), never an empty model string
    _now = datetime.datetime.now().astimezone()

    # Make bare relative paths ("pyproject.toml") in filesystem/read tools resolve against
    # the open project folder, not the server's process cwd. Set fresh every request (to the
    # value or None), so it self-resets between calls — no try/finally needed in the
    # sequential harness. ponytail: module global; switch to ContextVar if requests ever run
    # filesystem tools truly concurrently with differing project roots.
    from tools import filesystem as _fs
    _fs.set_project_root(project_root)

    _is_ide = bool(chat_id) and str(chat_id).startswith("ide_")
    _is_plan = bool(chat_id) and str(chat_id).startswith("plan_") or force_plan_mode
    _is_skill_or_direct = tools is not None or force_web_search or force_local_agent

    # Universal ambiguity guard: catches deictic references ("explain this process"),
    # sports/event ambiguity ("who won", "when is the game", "Arsenal beat Chelsea"),
    # ambiguous entities ("Python", "Apple"), food intent without service/location
    # ("I'm hungry", "bowl of rice"), and data analysis without a file ("run stats").
    # Runs before any LLM calls (orchestrated or not). Skipped in plan mode, direct
    # tool/skill overrides, or when images are attached — an attached image resolves
    # deictic references like "this image" that the text-only guard can't see and
    # would otherwise misflag as ambiguous.
    if not _is_plan and not _is_skill_or_direct and not images:
        has_history = False
        lock = None
        if chat_id is not None:
            lock = get_lock(chat_id)
            with lock:
                _prior_turns = conv_get(chat_id)
                has_history = bool(_prior_turns)
            query = _rewrite_weather_followup(query, _prior_turns)

        _guard_q = _guard_check(query, has_history=has_history)
        if _guard_q:
            _log.info("[guard] ambiguous/incomplete query — returning clarifying question")
            if chat_id is not None:
                with lock:
                    conv_append(chat_id, "user", query)
                    conv_append(chat_id, "assistant", _guard_q)
            return _guard_q, "guard", "unclear"

    # Image-attached chat message (e.g. a photo sent in Telegram/web UI), no manual
    # model pick: short-circuit the orchestrator's tool-calling loop entirely and
    # answer in one shot via CLOUD_VISION_MODEL. Deliberately skips the orchestrator
    # (rather than just handing it images) because that cheap flash-lite model's
    # agentic tool-use reliability is unverified (a same-family model scored 2/5 on
    # the tool-calling benchmark, see cloud_client.py) — only its raw single-shot
    # vision was tested and confirmed good. IDE mode keeps its own gemma4 vision path.
    if images and model is None and not _is_plan and not _is_skill_or_direct and not _is_ide:
        history = []
        lock = None
        if chat_id is not None:
            lock = get_lock(chat_id)
            with lock:
                history = trim_to_budget(conv_get(chat_id), cloud_client.CLOUD_VISION_MODEL, query, chat_id=chat_id)

        result = cloud_client.chat(
            user_message=query,
            images=images,
            model=cloud_client.CLOUD_VISION_MODEL,
            history=history,
            on_token=on_token,
            run_id=run_id,
        )

        if chat_id is not None:
            with lock:
                conv_append(chat_id, "user", query)
                conv_append(chat_id, "assistant", result)

        if tts:
            _speak(result, voice=tts_voice)

        return result, cloud_client.CLOUD_VISION_MODEL, "vision"

    # ponytail: parse use_diagram links in recovered assistant content and
    # tag them for panel rendering
    def _process_diagram_urls(text: str) -> str:
        import re
        diagram_url_pattern = re.compile(r'use_diagram:\s+(https?://[^/\s]+(?:/diagram/\d+)?)', re.IGNORECASE)
        return diagram_url_pattern.sub(r'{RENDER_DIAGRAM: \1}', text)

    if orchestrated and not _is_plan and not _is_skill_or_direct:
        orchestrator_model = model if (model and cloud_client.is_cloud_model(model)) else cloud_client.DEFAULT_CLOUD_MODEL
        
        # --- Read history under lock so no concurrent thread sees stale state ---
        history = []
        if chat_id is not None:
            lock = get_lock(chat_id)
            with lock:
                raw_history = conv_get(chat_id)
                # Trim to budget for cloud model (keeps history clean and focused).
                # Voice: shrink the verbatim window to cut prompt tokens per round
                # (~40% fewer history tokens → lower first-token latency). The
                # rolling summary still carries older context; ponytail: leaves a
                # transient 4-turn gap between the 6-turn window and the 10-turn
                # fold threshold, self-heals as the convo grows past 10.
                _keep = _VOICE_KEEP_RECENT if chat_id == "brabble_voice" else KEEP_RECENT_TURNS
                history = trim_to_budget(raw_history, orchestrator_model, query, chat_id=chat_id, keep_recent=_keep)

        # Set the ContextVar for subagents
        CURRENT_CHAT_ID.set(chat_id)

        _log.info("[orchestrator] run_id=%s chat_id=%s model=%s history_turns=%d query=%r", run_id, chat_id, orchestrator_model, len(history), query[:120])

        from tools._time_context import now_line
        _dt_line = now_line(_now)
        _home = str(Path.home())
        _orch_fragments = _select_orchestrator_fragments(query, chat_id)
        # Short simple queries don't need deep_research — gate both the tool
        # description in the prompt AND its presence in the tools array on the
        # same boolean (see below), so the model never sees a tool described
        # here that isn't actually callable this round (was previously two
        # independent checks that could disagree — confirmed live, the model
        # would call ask_deep_research anyway, get rejected, self-recover).
        _is_simple_query = len(query.strip()) < 80 and not re.search(
            r"\b(deep\s*research|comprehensive\s*(report|analysis)|literature\s*review|"
            r"thorough\s*investigat|in\s*depth|detailed\s*report|research\s+paper|"
            r"multi[-\s]?step\s+research)\b", query, re.IGNORECASE
        )
        _deep_research_line = "" if _is_simple_query else (
            "15. `ask_deep_research(query, depth, breadth)`: Perform deep research, "
            "multi-step parallel search, gap analysis, and compile a comprehensive "
            "report on a complex topic."
        )
        _deep_research_critical = "" if _is_simple_query else (
            "- For detailed, comprehensive research requests, literature reviews, "
            "or academic questions, use ask_deep_research."
        )
        orchestrator_system_prompt = _dt_line + "\n\n" + ORCHESTRATOR_SYSTEM_PROMPT.format(
            home_dir=_home, fragments=_orch_fragments,
            deep_research_line=_deep_research_line, deep_research_critical=_deep_research_critical,
        )
        # 2026-07-13: the orchestrated branch returns (see below) before ever reaching
        # memory_recall(query) further down in run() — that call only fires on the
        # non-orchestrated fallback path. Since cloud-first routing makes orchestrator
        # the default for nearly everything, passive cross-session recall was silently
        # dead for the common case. Confirmed live: a "what is my favorite X" follow-up
        # got zero automatic memory context and had to fall back to an explicit
        # search_sessions call (which searches the wrong source, see memory_tools.py).
        _orch_mem_block = memory_recall(query)
        if _orch_mem_block:
            orchestrator_system_prompt = _orch_mem_block + "\n" + orchestrator_system_prompt
        _orch_activity_block = recent_activity_block()
        if _orch_activity_block:
            orchestrator_system_prompt = _orch_activity_block + "\n" + orchestrator_system_prompt
        # Mid-task plan/todo scratchpad (store/plan_store.py) — empty on a fresh
        # run_id, populates once update_task_plan is called. Injected here (not
        # re-built per tool round — the system prompt is fixed for this local_chat
        # call) so a verify-on-absence/claim-check retry, which re-enters run()
        # with the same run_id, sees the plan state left by the first pass.
        from store import plan_store as _plan_store
        _orch_plan_block = _plan_store.plan_block(run_id)
        if _orch_plan_block:
            orchestrator_system_prompt = _orch_plan_block + "\n" + orchestrator_system_prompt
        if intent == "agent_message":
            orchestrator_system_prompt += (
                "\n\nYou are processing an autonomous message from another agent. "
                "Your job: verify the claim using your tools, then decide "
                "whether it is significant enough to notify the user. "
                "If significant, call send_telegram with a concise alert. "
                "Trust but verify — do NOT forward raw agent output verbatim. "
                "Synthesize: what changed, why it matters, and what (if anything) "
                "the user should do."
            )
        if chat_id == "brabble_voice":
            # 2026-07-11: brabble_hook.py's _clean_for_tts() strips bold/italic/
            # code/heading markers but not table pipes or horizontal rules, so a
            # markdown table (observed: gold-price query, "| Item | Value |"
            # rows) reaches Kokoro TTS near-verbatim and reads as garbled noise.
            # Root cause is generation, not cleanup — tell the model not to
            # produce table/heading/rule formatting for this channel at all.
            orchestrator_system_prompt += (
                "\n\nVOICE OUTPUT MODE: your reply will be spoken aloud by "
                "text-to-speech, not displayed as text. Answer in plain spoken "
                "sentences only — no markdown tables, headings, bullet lists, "
                "horizontal rules, or bold/italic markers. Say numbers and "
                "prices in words a listener can follow (e.g. \"four thousand, "
                "one hundred twenty one dollars\" not a table row).\n\n"
                "DEFAULT TO SHORT ANSWERS: this is a voice conversation, not a "
                "document — a listener can't skim or scroll back. HARD LIMIT: "
                "under 60 words / 4 sentences by default, even for "
                "broad-sounding questions ('pros and cons of X vs Y', 'rundown "
                "of Z') — pick the 1-2 biggest factors, not an exhaustive "
                "category-by-category breakdown (confirmed live 2026-07-11: a "
                "full pros/cons rundown took 130-140+ seconds of spoken audio "
                "for one question — nobody wants that by default). Only exceed "
                "60 words when the user's wording explicitly asks for more — "
                "'give me the full/detailed/complete rundown', 'go in depth', "
                "'don't leave anything out', 'tell me everything about X', or "
                "similar — an explicit ask has no limit."
            )

        from tools.orchestrator_tools import (
            ASK_LOCAL_AGENT_SCHEMA, ASK_READ_FILE_SCHEMA, ASK_WRITE_FILE_SCHEMA,
            ASK_DELETE_FILE_SCHEMA, ASK_RENAME_FILE_SCHEMA, ASK_RUN_COMMAND_SCHEMA,
            ASK_SEND_MESSAGE_SCHEMA,
            ASK_FETCH_URL_SCHEMA,
            ASK_WEB_SEARCH_SCHEMA, ASK_BROWSER_AGENT_SCHEMA,
            ASK_MACOS_NATIVE_SCHEMA, ASK_EMAIL_AGENT_SCHEMA, ASK_TASKS_AGENT_SCHEMA,
            ASK_CALENDAR_AGENT_SCHEMA, ASK_DOCS_AGENT_SCHEMA, ASK_SHEETS_AGENT_SCHEMA,
            ASK_DEEP_RESEARCH_SCHEMA,
            ASK_AUTOMATION_AGENT_SCHEMA, ASK_DEV_AGENT_SCHEMA, ASK_MESSAGING_AGENT_SCHEMA,
            ASK_RESEARCH_AGENT_SCHEMA, ASK_VISION_AGENT_SCHEMA, ASK_UTILITY_AGENT_SCHEMA,
            ASK_YOUTUBE_AGENT_SCHEMA, ASK_TRANSPORT_AGENT_SCHEMA,
            ASK_X_AGENT_SCHEMA,
            ASK_REDDIT_AGENT_SCHEMA, ASK_SCIENCE_SCOUT_AGENT_SCHEMA,
            QUERY_RECENT_TRACES_SCHEMA,
        )
        from tools.opencode_tool import ASK_OPENCODE_SCHEMA
        from tools.set_opencode_model import SCHEMA as SET_OPENCODE_MODEL_SCHEMA
        from tools.diagram_render import RENDER_DIAGRAM_SCHEMA
        from tools.telegram_send import SEND_TELEGRAM_SCHEMA
        from tools.contacts_resolver import SCHEMA as CONTACTS_RESOLVE_SCHEMA
        # 2026-07-17: registered in skills_hub/EXECUTORS but never added to this
        # list — same class of silent-omission bug as remember/forget/
        # ask_transport_agent above. Needed so the orchestrator can pull the
        # Automation Edge Case Checklist skill (see ORCHESTRATOR_SYSTEM_PROMPT's
        # create_automation bullet) without a full run_skill sub-agent hop.
        from skills_hub import SKILLS_MATCH_SCHEMA
        # ponytail: read/status automation tools exposed directly on the
        # orchestrator so "list/pause/resume my automations" is 2 round-trips
        # (call + format) instead of 4 (orchestrator→subagent→re-format). These
        # are cheap DB ops that don't need subagent isolation. create/delete/
        # update stay behind ask_automation_agent — delete is force-routed there
        # (see _DELETE_AUTOMATION_RE below) to stop the narrate-without-calling bug.
        from tools.automation_tools import (
            LIST_AUTOMATIONS_SCHEMA, GET_AUTOMATION_SCHEMA, GET_AUTOMATION_HISTORY_SCHEMA,
            PAUSE_AUTOMATION_SCHEMA, RESUME_AUTOMATION_SCHEMA,
            CREATE_AUTOMATION_SCHEMA, TRIGGER_AUTOMATION_NOW_SCHEMA,
            DELETE_AUTOMATION_SCHEMA, UPDATE_AUTOMATION_SCHEMA,
            LIST_PENDING_APPROVALS_SCHEMA, APPROVE_WORKFLOW_SCHEMA, REJECT_WORKFLOW_SCHEMA,
            QUERY_SUBAGENT_FINDINGS_SCHEMA,
            GET_AUTOMATION_STATS_SCHEMA, GET_WORKFLOW_FAILURES_SCHEMA,
            GET_WORKFLOW_LOGS_SCHEMA, GET_INTEGRATION_TEMPLATES_SCHEMA,
        )
        orchestrator_tools = [
            LIST_AUTOMATIONS_SCHEMA, GET_AUTOMATION_SCHEMA, GET_AUTOMATION_HISTORY_SCHEMA,
            PAUSE_AUTOMATION_SCHEMA, RESUME_AUTOMATION_SCHEMA,
            # Draft-and-send confirm/reject — same cheap-DB-op tier as list/pause/
            # resume above, no reason to force a subagent hop for "what's pending"
            # or "approve it".
            LIST_PENDING_APPROVALS_SCHEMA, APPROVE_WORKFLOW_SCHEMA, REJECT_WORKFLOW_SCHEMA,
            QUERY_SUBAGENT_FINDINGS_SCHEMA,
            # 2026-07-31: observability (stats/failures/logs/templates) — cheap DB
            # reads, exposed direct so the orchestrator can self-diagnose ("why did
            # my automation fail?") in 2 round-trips instead of a subagent hop.
            GET_AUTOMATION_STATS_SCHEMA, GET_WORKFLOW_FAILURES_SCHEMA,
            GET_WORKFLOW_LOGS_SCHEMA, GET_INTEGRATION_TEMPLATES_SCHEMA,
            # 2026-07-11: create/trigger/delete/update were previously only reachable
            # via ask_automation_agent (a full extra subagent round-trip) while
            # list/pause/resume were already direct — an inconsistent gate, not a
            # deliberate safety boundary (confirmed live: a simple "trigger automation
            # X" took 19.27s / 5 LLM round-trips through the subagent hop vs. ~5s for
            # the already-direct list_automations). delete_workflow_permanently stays
            # subagent-only — that one's a genuinely irreversible hard-delete, worth
            # gating.
            CREATE_AUTOMATION_SCHEMA, TRIGGER_AUTOMATION_NOW_SCHEMA,
            DELETE_AUTOMATION_SCHEMA, UPDATE_AUTOMATION_SCHEMA,
            # 2026-07-11: create_automation can't express "save a file"/multi-step
            # actions (only telegram/notification/http_webhook/email) — without
            # these, the orchestrator had no way to discover or use an existing
            # internal handler pipeline for a request that needs more than that,
            # and (confirmed live) fabricated a false capability claim into the
            # automation it created instead. See the ORCHESTRATOR_SYSTEM_PROMPT
            # bullet on create_automation's limits for the paired instruction.
            *[t for t in ALL_TOOLS if t["function"]["name"] in ("create_workflow", "list_available_pipelines")],
            ASK_LOCAL_AGENT_SCHEMA, ASK_READ_FILE_SCHEMA, ASK_WRITE_FILE_SCHEMA,
            ASK_DELETE_FILE_SCHEMA, ASK_RENAME_FILE_SCHEMA, ASK_RUN_COMMAND_SCHEMA,
            ASK_FETCH_URL_SCHEMA,
            ASK_WEB_SEARCH_SCHEMA, ASK_BROWSER_AGENT_SCHEMA,
            ASK_MACOS_NATIVE_SCHEMA, ASK_EMAIL_AGENT_SCHEMA, ASK_TASKS_AGENT_SCHEMA,
            ASK_CALENDAR_AGENT_SCHEMA, ASK_DOCS_AGENT_SCHEMA, ASK_SHEETS_AGENT_SCHEMA,
            ASK_DEEP_RESEARCH_SCHEMA,
            ASK_AUTOMATION_AGENT_SCHEMA, ASK_DEV_AGENT_SCHEMA, ASK_MESSAGING_AGENT_SCHEMA,
            ASK_RESEARCH_AGENT_SCHEMA, ASK_VISION_AGENT_SCHEMA, ASK_UTILITY_AGENT_SCHEMA,
            ASK_YOUTUBE_AGENT_SCHEMA, ASK_X_AGENT_SCHEMA,
            ASK_OPENCODE_SCHEMA,
            SET_OPENCODE_MODEL_SCHEMA,
            ASK_TRANSPORT_AGENT_SCHEMA,
            ASK_REDDIT_AGENT_SCHEMA, ASK_SCIENCE_SCOUT_AGENT_SCHEMA,
            QUERY_RECENT_TRACES_SCHEMA,
            ASK_SEND_MESSAGE_SCHEMA,
            *[t for t in ALL_TOOLS if t["function"]["name"] in ("remember", "forget", "search_sessions")],
            *[t for t in ALL_TOOLS if t["function"]["name"] == "update_task_plan"],
            *[t for t in ALL_TOOLS if t["function"]["name"] == "call_my_phone"],
            RENDER_DIAGRAM_SCHEMA,
            CONTACTS_RESOLVE_SCHEMA,
            SKILLS_MATCH_SCHEMA,
            SEND_TELEGRAM_SCHEMA,
    ]

        # _is_simple_query computed earlier (before the system prompt was built)
        # so the prompt's deep_research description and this tools-array strip
        # agree on the same call.
        if _is_simple_query:
            orchestrator_tools = [t for t in orchestrator_tools
                                  if t["function"]["name"] != "ask_deep_research"]

        # The orchestrated block returns before ever reaching the classify_message()
        # call further down (that one only runs for non-orchestrated callers) — so
        # `intent` here is still just whatever the caller passed as a parameter
        # (None for callers that don't pre-classify, e.g. the web UI; Telegram/
        # WhatsApp pass their own pre-computed intent after calling
        # classify_message() themselves). Classify here, once, only when the
        # caller didn't already — the _forced_tool block right below needs a real
        # intent value to trust, not the bare parameter default.
        if intent is None:
            intent = classify_message(query, channel=channel).intent

        # Nothing should hit the bare model on a query that needs live grounding
        # (scores, news, prices, "who won") — the orchestrator otherwise lets the
        # model freely decide whether to call a tool, and it has been observed
        # skipping that and fabricating an answer from stale training knowledge
        # (2026-07 WhatsApp World Cup hallucination). Reuse the same _is_temporal()
        # detector classify() already uses for this, and force the first tool call
        # when it fires. Sports specifically gets ask_research_agent (Sofascore),
        # not ask_web_search — confirmed live 2026-07: scraped web results for a
        # live/recent match are frequently stale or mutually contradictory across
        # sites, while Sofascore's structured API resolved the same query cleanly
        # once the user explicitly asked for it. Everything else temporal still
        # forces ask_web_search.
        # Same class of bug as the temporal case above: asked to delete an
        # automation/workflow, the model has been observed narrating success
        # ("has been permanently deleted") without ever calling ask_automation_agent
        # at all, leaving the row untouched (confirmed live 2026-07). Force the
        # dispatcher tool — the orchestrator only sees ask_automation_agent, the
        # actual delete_automation/delete_workflow_permanently tools live one
        # level down inside that subagent's own tool loop.
        # 2026-07-11: this used to re-derive "is this temporal/automation" via its
        # own independent raw-text regex (_AUTOMATION_RE.search(query) / _is_temporal
        # (query)), separate from whatever classify()/classify_message() already
        # decided — that duplication was the actual root cause of a blindspot bug
        # class (an automation named "Daily Tech News Brief" tripped _TEMPORAL_RE's
        # bare "news" match, forcing a wasted ask_web_search on a pause command,
        # since raw-regex has no notion that "news" was inside a proper noun, not a
        # topic). `intent` by this point is LLM-classified (semantic, reads the whole
        # sentence), not keyword-matched — trust it instead of re-scanning raw text.
        # _SPORTS_RE stays regex: a narrow, stable lexical sub-check *within* an
        # already-semantically-determined "temporal" intent, not a substitute for it.
        _forced_tool = None
        # 2026-07-14: opencode-model switch — intercept "use nemotron/tron model" etc.
        # before the intent gate (which would route to ask_automation_agent and loop).
        if not images and _OPENCODE_MODEL_RE.search(query) and _OPENCODE_MODEL_TOKEN_RE.search(query):
            _forced_tool = "set_opencode_model"
        elif not images and intent == "automation":
            _forced_tool = "ask_automation_agent"
        elif not images and intent == "temporal":
            _forced_tool = "ask_research_agent" if _SPORTS_RE.search(query) else "ask_web_search"
        # Agent message inbox poll — inject pending messages into context
        # so the orchestrator proactively addresses findings from background
        # agents without needing an explicit user query about them.
        if chat_id is not None and not _is_plan:
            try:
                from jobs.agent_message_queue import poll_messages, mark_read, format_inbox
                _pending = poll_messages(to_agent="orchestrator", status="unread", limit=5)
                if _pending:
                    mark_read([m["id"] for m in _pending])
                    _inbox_text = format_inbox(_pending)
                    orchestrator_system_prompt += (
                        "\n\n## Pending Agent Messages (" + str(len(_pending)) + ")\n"
                        "The following messages arrived from background agents "
                        "since your last turn. Address them proactively if relevant, "
                        "or mention them to the user.\n"
                        + _inbox_text
                    )
                    _log.info("injected %d pending agent messages into context", len(_pending))
            except Exception:
                _log.warning("agent inbox poll failed", exc_info=True)

        _round_timeout = round_timeout or (28.0 if chat_id == "brabble_voice" else None)
        result = local_chat(
            user_message=query,
            tools=orchestrator_tools,
            model=orchestrator_model,
            history=history,
            on_token=on_token,
            system_prompt=orchestrator_system_prompt,
            max_rounds=12,
            images=images or None,
            options=_get_optimized_opts("factual_qa", orchestrator_model),
            run_id=run_id,
            force_tool_choice=_forced_tool,
            round_timeout=_round_timeout,
        )

        # Verify-on-absence / verify-on-undercoverage: one retry, straight-line (no recursion/loop possible).
        _missing = _absence_unchecked_sources(result, query, run_id, intent)
        if _missing:
            _log.warning("[verify-on-absence] run_id=%s missing=%s — retrying with nudge", run_id, sorted(_missing))
            result = local_chat(
                user_message=query,
                tools=orchestrator_tools,
                model=orchestrator_model,
                history=history,
                on_token=on_token,
                system_prompt=orchestrator_system_prompt + (
                    "\n\nVERIFICATION REQUIRED: a draft answer claimed nothing was found, "
                    f"but these sources were never checked: {', '.join(sorted(_missing))}. "
                    "Call them now, then give your final answer based on ALL sources."
                ),
                max_rounds=12,
                images=images or None,
                options=_get_optimized_opts("factual_qa", orchestrator_model),
                run_id=run_id,
                round_timeout=_round_timeout,
            )
        else:
            # Only run the claim-check if verify-on-absence didn't already retry —
            # one adversarial pass per run, same invariant as above.
            _claim_trace = _needs_claim_check(query, run_id, intent)
            if _claim_trace:
                _discrepancy = _check_claim_against_trace(result, _claim_trace)
                if _discrepancy:
                    _log.warning("[claim-check] run_id=%s discrepancy=%s — retrying with nudge", run_id, _discrepancy)
                    result = local_chat(
                        user_message=query,
                        tools=orchestrator_tools,
                        model=orchestrator_model,
                        history=history,
                        on_token=on_token,
                        system_prompt=orchestrator_system_prompt + (
                            f"\n\nVERIFICATION REQUIRED: your previous draft answer had a discrepancy "
                            f"with the actual tool results: {_discrepancy}. Re-check the tool results and "
                            "give a corrected final answer."
                        ),
                        max_rounds=12,
                        images=images or None,
                        options=_get_optimized_opts("factual_qa", orchestrator_model),
                        run_id=run_id,
                        round_timeout=_round_timeout,
                    )

        if chat_id == "brabble_voice":
            _trimmed = _voice_trim_reply(result, query)
            if _trimmed != result:
                _log.info(
                    "[voice-trim] run_id=%s reply %d words -> %d words (no detail request in query)",
                    run_id, len(result.split()), len(_trimmed.split()),
                )
            result = _trimmed

        if chat_id is not None:
            with lock:
                conv_append(chat_id, "user", query)
                conv_append(chat_id, "assistant", result)
                compact_old_turns(chat_id, orchestrator_model)

        if tts:
            _speak(result, voice=tts_voice)

        # ponytail: post-process any captured diagram URLs into render_diagram calls
        result = _process_diagram_urls(result)
        return result, orchestrator_model, "orchestrator"

    _is_ide = bool(chat_id) and str(chat_id).startswith("ide_")
    _is_plan = bool(chat_id) and str(chat_id).startswith("plan_") or force_plan_mode
    _ide_auto = _is_ide and not model  # model is None or ""
    _manual_ide = _is_ide and not _ide_auto

    if _is_plan:
        # Plan mode: cloud LLM + plan intent (read-only analysis)
        routed_model, intent = cloud_client.DEFAULT_CLOUD_MODEL, "plan"
    elif intent is not None:
        # Caller already ran clients.router.classify_message() — don't re-derive intent
        # from the same text a second time (this is what used to make Telegram/WhatsApp's
        # classify_message() opinion get silently discarded and re-classified here).
        routed_model = model or model_for_intent(intent)
    elif _ide_auto:
        routed_model, intent = classify_ide(query)
    elif _manual_ide:
        # User picked a specific model in the IDE chat — still classify for tools
        _, intent = classify_ide(query)
        routed_model = model
    elif model is None:
        # LLM-primary classification (classify_message: one cloud call, semantic —
        # not keyword regex), regex cascade only as its own internal fallback on
        # failure/timeout. Replaces a direct classify(query) call here 2026-07-11 —
        # the old bare-regex path was the root cause of a class of blindspot bugs
        # (e.g. _TEMPORAL_RE matching "news" inside an automation's own name,
        # "Daily Tech News Brief", forcing a wasted search on a pause command).
        _cls = classify_message(query, channel=channel)
        routed_model, intent, specialist_hint = _cls.model, _cls.intent, _cls.specialist_hint
    else:
        # Still classify for intent/tools — manual only locks the model, not the tools
        _cls = classify_message(query, channel=channel)
        intent, specialist_hint = _cls.intent, _cls.specialist_hint
        routed_model = model

    if force_local_agent:
        intent = "filesystem"
        if model is None:
            routed_model = ollama_client.DEFAULT_MODEL

    _log.info("[router] run_id=%s chat_id=%s intent=%s model=%s", run_id, chat_id, intent, routed_model)

    _ide_override = _is_ide  # used downstream for system_prompt + max_rounds

    # Vision: if images are attached and no model was manually selected, route to
    # the cheap cloud vision model (verified 2026-07-05 — see cloud_client.py's
    # CLOUD_VISION_MODEL note). IDE mode keeps gemma4 since it's local/offline by
    # design there; only tested against synthetic OCR-style text-in-image so far,
    # not real scanned forms — form-filling's own vision tools are untouched by
    # this and still route through gemma4 separately.
    if images and model is None and not _is_ide:
        routed_model, intent = cloud_client.CLOUD_VISION_MODEL, "vision"

    msg_lower = query.lower()
    length = len(query.strip())

    _tool = lambda *names: [t for t in ALL_TOOLS if t["function"]["name"] in set(names)]

    _FS_TOOL_NAMES = tools_with_tags("fs")
    _FS_TOOL_SCHEMAS = [t for t in ALL_TOOLS if t["function"]["name"] in _FS_TOOL_NAMES]

    _IDE_TOOL_NAMES = tools_with_tags("fs", "ide_extra")
    _IDE_TOOL_SCHEMAS = [t for t in ALL_TOOLS if t["function"]["name"] in _IDE_TOOL_NAMES]

    _BROWSER_TOOL_NAMES = tools_with_tags("browser")

    _TRANSIT_TOOL_NAMES = tools_with_tags("transit")

    _GIT_TOOL_NAMES = tools_with_tags("git")
    _REPL_TOOL_NAMES = tools_with_tags("repl")

    # Chat mode = Telegram or web UI (not IDE). These get lighter, faster models
    # for intents that don't need file access.
    _chat_mode = not _is_ide

    if intent == "library_docs":
        active_tools = _tool("get_library_docs", "local_search")
        if not _chat_mode:
            routed_model = ollama_client.DEFAULT_MODEL
        # chat mode: qwen3:4b handles docs well with get_library_docs tool
    elif intent == "reminder":
        active_tools = _tool("set_reminder", "get_current_time")
    elif intent == "calendar":
        active_tools = _tool(
            "list_calendar_events",
            "create_calendar_event",
            "delete_calendar_event",
            "list_mac_calendar_events",  # native macOS Calendar fallback
            "get_current_time",
            "refresh_google_token",
        )
    elif intent == "tasks":
        active_tools = _tool(
            "list_tasks",
            "create_task",
            "complete_task",
            "delete_task",
            "list_reminders",  # native macOS Reminders
            "create_reminder",
            "get_current_time",
            "refresh_google_token",
        )
    elif intent == "docs":
        active_tools = _tool(
            "list_google_docs",
            "read_google_doc",
            "create_google_doc",
            "append_google_doc",
            "update_google_doc_title",
            "refresh_google_token",
        )
    elif intent == "sheets":
        active_tools = _tool(
            "list_google_sheets",
            "read_google_sheet",
            "create_google_sheet",
            "append_google_sheet",
            "update_google_sheet_cell",
            "refresh_google_token",
        )
    elif intent == "email":
        # get_latest_email is a composite (list+read in one call) — reduces the
        # get_latest_email is a composite (list+read) — reduces chain to 2 steps.
        # list_email_attachments + semantic_file_search added 2026-07: this intent
        # previously had zero filesystem visibility, so "what did I spend on X"
        # style questions (answerable from the already-downloaded/indexed
        # ~/Documents/email-attachments archive) fell through to the top-level
        # orchestrator's raw shell/OCR tools instead — root cause of a crash, see
        # CLAUDE.md Known Issues.
        active_tools = _tool(
            "get_latest_email",
            "list_emails",
            "read_email",
            "send_email",
            "send_telegram",
            "refresh_google_token",
            "list_email_attachments",
            "semantic_file_search",
            "ask_fetch_url",
        )
    elif intent == "git":
        active_tools = [t for t in ALL_TOOLS if t["function"]["name"] in _GIT_TOOL_NAMES]
        if model is None:
            routed_model = cloud_client.DEFAULT_CLOUD_MODEL
    elif intent == "plan":
        # Plan mode: read-only tools only
        active_tools = PLAN_TOOLS
        # Already using gemma4 from plan detection above
        # Set plan mode flag for tool blocking
        import tools.registry as _reg

        _reg.PLAN_MODE_ACTIVE = True
    elif intent == "repl":
        active_tools = [t for t in ALL_TOOLS if t["function"]["name"] in _REPL_TOOL_NAMES]
        if model is None:
            routed_model = cloud_client.DEFAULT_CLOUD_MODEL
    elif tools is not None:
        active_tools = [t for t in ALL_TOOLS if t["function"]["name"] in tools]
        if active_tools and model is None:
            # Cloud primary; local_chat() auto-falls-back to ollama DEFAULT_MODEL
            # if cloud is unreachable — see local_chat() docstring.
            routed_model = cloud_client.DEFAULT_CLOUD_MODEL
    elif intent == "math":
        active_tools = []
    elif intent == "filesystem":
        # Always needs FS read tools regardless of context
        active_tools = _FS_TOOL_SCHEMAS
        # gemma4 for filesystem (better tool use, already set above)
    elif intent == "automation":
        active_tools = _tool(
            "read_file",
            "send_telegram",
            "create_task",
            "get_current_time",
            "refresh_google_token",
            "list_pdf_attachments",
            "convert_pdf",
            "list_emails",
            "read_email",
            "run_inbox_monitor",
            "create_automation",
            "list_automations",
            "get_automation",
            "get_automation_history",
            "update_automation",
            "pause_automation",
            "resume_automation",
            "delete_automation",
            "trigger_automation_now",
            "delete_workflow_permanently",
            # Draft-and-send: 'approval' workflow steps pause a run until a
            # human confirms (see jobs/handlers/user_automation.py + the
            # 'approval' step docs in create_automation's own schema) — these
            # were registered in ALL_TOOLS/EXECUTORS but never added to this
            # subagent's active-tools list, so "what's pending" / "approve
            # it" / "reject it" had no tool to call from chat. Same class of
            # omission as the 2026-07-13 remember/forget/ask_transport_agent
            # fix documented in CLAUDE.md.
            "list_pending_approvals",
            "approve_workflow",
            "reject_workflow",
            # 2026-07-11: this subagent could only build automations from
            # create_automation's fixed action_type enum (telegram/notification/
            # http_webhook/email) — confirmed live: a "watch sender + save
            # attachment + notify" request has no matching action_type, and the
            # subagent had no way to discover or reuse an already-registered
            # internal handler pipeline (e.g. email.attachment_watch) that does
            # exactly this, even though it knew such logic existed. These let it
            # check the real pipeline catalog and schedule one directly instead
            # of degrading to a partial automation or asking the user to choose.
            "list_available_pipelines",
            "create_workflow",
            "list_workflows",
            "take_screenshot",
            "list_windows",
        )
    elif intent == "browser":
        active_tools = [t for t in ALL_TOOLS if t["function"]["name"] in _BROWSER_TOOL_NAMES]
        if model is None:
            # Cloud primary; local_chat() auto-falls-back to ollama gemma4 if
            # cloud is unreachable — see local_chat() docstring.
            routed_model = cloud_client.DEFAULT_CLOUD_MODEL
    elif intent == "transit":
        active_tools = [t for t in ALL_TOOLS if t["function"]["name"] in _TRANSIT_TOOL_NAMES]
        if model is None:
            routed_model = cloud_client.DEFAULT_CLOUD_MODEL
    elif intent == "ocr":
        active_tools = _tool("ocr_image", "take_screenshot", "parse_document")
    elif intent == "vision":
        active_tools = _tool(
            "take_screenshot", "list_windows", "ocr_image", "parse_document",
            "vision_update_form_fields", "spotlight_search",
        )
        # Text-only vision requests (e.g. "take a screenshot and read it") need
        # tool-calling to take_screenshot first, then usually a follow-up call
        # once the image exists (multi-round). CLOUD_VISION_MODEL (gemini-3.1-
        # flash-lite) is requested here specifically so the same model that
        # reads the screenshot also drives the tool loop — but it's documented
        # in cloud_client.py as 2/5 on the agentic tool-calling benchmark
        # (repetitive-loop drift), verified only for single-shot OCR reads, not
        # multi-round chains like this one. ponytail: shipped as asked; if the
        # screenshot->read flow starts drifting/looping, that benchmark result
        # is why — the fix would be deepseek/haiku for the outer tool loop with
        # CLOUD_VISION_MODEL only for the final image-reading call.
        # CLOUD_FALLBACK_MODEL == CLOUD_VISION_MODEL (same OpenRouter model) —
        # cloud_client.chat()'s `if model == fallback_model: raise` guard means
        # a failure here skips the usual cloud-fallback hop and goes straight to
        # local_chat()'s outer cloud->gemma4 fallback. No self-retry loop risk.
        if model is None and not images:
            routed_model = cloud_client.CLOUD_VISION_MODEL
    elif intent == "slack":
        active_tools = _tool("slack_search", "slack_status")
    elif intent == "imessage":
        active_tools = _tool(
            "imessage_search", "imessage_status", "imessage_send",
        )
    elif intent == "whatsapp_search":
        active_tools = _tool("whatsapp_search", "whatsapp_status")
    elif intent == "spotlight":
        active_tools = _tool(
            "spotlight_search", "find_recent", "archive_grep",
        )
    elif intent == "dev_tools":
        active_tools = [
            t for t in ALL_TOOLS
            if t["function"]["name"] in (_GIT_TOOL_NAMES | _REPL_TOOL_NAMES)
        ] + _tool("list_windows", "take_screenshot")
        if model is None:
            routed_model = cloud_client.DEFAULT_CLOUD_MODEL
    elif intent == "messaging":
        active_tools = _tool(
            "imessage_search", "imessage_status", "imessage_send",
            "whatsapp_search", "whatsapp_status", "send_whatsapp",
            "slack_search", "slack_status",
            "set_reminder", "get_current_time",
        )
    elif intent == "research_connectors":
        active_tools = _tool(
            "search_wikipedia", "search_wikidata", "search_crossref",
            "search_openalex", "search_opencorporates", "search_sec_edgar",
            "search_gdelt_news", "searxng_search",
            "scrapling_fetch", "scrapling_stealthy_fetch",
            "scrapling_extract", "scrapling_fetch_and_extract",
            "compile_research_report", "deep_research",
            "get_library_docs", "local_search",
            "sofascore_get_event", "sofascore_get_standings",
            "sofascore_get_team", "sofascore_get_tournaments",
            "sofascore_live_scores", "sofascore_scheduled_events",
            "sofascore_search_teams",
        )
    elif intent == "utility":
        active_tools = _tool(
            "system_status", "geocode_address", "generate_local_image",
            "analyze_directory", "suggest_organization", "apply_organization",
        )
    elif intent == "macos_native":
        active_tools = _tool(
            "clipboard_read", "clipboard_write",
            "list_safari_tabs",
            "list_notes", "notes_create",
            "applescript_run",
        )
    elif intent == "system_status":
        active_tools = _tool("system_status")
    elif intent in ("code_quick", "code_medium", "code_heavy"):
        active_tools = []  # local agent manages its own code toolset
    elif intent == "factual_qa":
        active_tools = _tool("web_search")  # Route factual Q&A to search graph
    elif intent == "youtube":
        active_tools = _tool("search_youtube", "get_youtube_transcript")
    elif intent == "x_search":
        active_tools = _tool("x_search", "x_read_tweet", "x_user_tweets")
    elif intent == "chinese_web":
        active_tools = _tool("search_chinese_web")
    elif intent == "analysis":
        # If the query is about screenshots/screen capture, override to vision tools.
        _screen_re = re.compile(r"\b(screenshot|screen\s*shot|capture\s*(the\s*)?(screen|display)|what.*screen|analyze.*screen|see.*screen)\b", re.I)
        if _screen_re.search(query):
            active_tools = _tool("take_screenshot", "ocr_image")
        else:
            active_tools = []  # analysis from training data; temporal/recent handles time-sensitive
    elif intent == "ide":
        # IDE chat: full read/write/bash access so the agent can explore and edit the project
        active_tools = _IDE_TOOL_SCHEMAS
    elif intent == "document":
        active_tools = _tool("parse_document", "ocr_image")
    elif intent in ("temporal", "recent") or intent.startswith("temporal_"):
        # Temporal + recent queries need web search + warm fast model.
        # "recent" events (2025+) are outside most models' training cutoff (late 2024).
        active_tools = _tool("web_search")
        if model is None:
            routed_model = cloud_client.DEFAULT_CLOUD_MODEL
    else:
        # Casual/chat: no tools. Prevents developer role + activation phrase from
        # being prepended, and allows num_predict=100 cap to apply. Skills hub
        # discovery is only needed when user explicitly asks about capabilities,
        # handled by the hub_tools injection below.
        active_tools = []

    # Web UI manual model: strip IDE-only tools (ocr, git, repl, filesystem, ide).
    # These intents get classified but their tools require filesystem/kernel access
    # that chat-mode models can't use — passing them causes empty/hung responses.
    # Gated on manual_model_pick, not "model is not None" — Telegram/WhatsApp always pass a
    # concrete model string too (classify_telegram()'s routing choice), which used to trip
    # this guard and silently strip git/repl/filesystem tools from every mobile message.
    _manual_web = manual_model_pick and _chat_mode
    if _manual_web and not force_local_agent and intent in ("git", "repl", "filesystem", "ide"):
        active_tools = []

    # Skills hub tools are always available for interactive intents so the agent can
    # discover its own capabilities and help users find the right skill for their task.
    if active_tools and intent not in ("search", "browser", "plan"):
        _hub_tools = _tool("list_available_skills", "match_skill_for_task")
        active_tools = list(active_tools) + _hub_tools

    # UI web-search toggle: route directly into the search graph (same path as temporal)
    if force_web_search:
        intent = "temporal"
        active_tools = _tool("web_search")  # keeps active_tools truthy so search graph fires
        routed_model = cloud_client.DEFAULT_CLOUD_MODEL
    elif _should_force_health_websearch(query, intent):
        intent = "temporal"
        active_tools = _tool("web_search")
        routed_model = cloud_client.DEFAULT_CLOUD_MODEL

    # Persistent memory: expose remember/forget alongside any active toolset, and enable
    # them for otherwise-toolless chat only when the user explicitly asks to remember/forget
    # (keeps the fast no-tools path for ordinary chat). Excludes constrained pipelines.
    if intent not in ("search", "browser", "plan"):
        if active_tools:
            active_tools = list(active_tools) + _tool("remember", "forget")
        elif _MEMORY_TRIGGER_RE.search(query):
            active_tools = _tool("remember", "forget")

    # Build IDE system prompt: coding agent with full read/write/bash access
    system_prompt = None
    if _ide_override and project_root:
        system_prompt = (
            f"You are a coding agent with read/write filesystem access and bash execution.\n"
            f"The project is at {project_root}.\n"
            f"Rules:\n"
            f"- The active file path and its content are already provided in [Active file:]. "
            f"Read it from there — do NOT call read_file on it again.\n"
            f"- For single-file tasks: go directly to edit_file or write_file. "
            f"Skip file_tree and extra reads.\n"
            f"- Only call file_tree('{project_root}') when you genuinely need to understand "
            f"the project layout (e.g. cross-file refactors, finding related files).\n"
            f"- Use append_file to add content at the END of a file (no need for old_str).\n"
            f"- Use edit_file (exact find+replace) for changes in the middle of a file.\n"
            f"- Use write_file only for brand-new files.\n"
            f"- After editing, verify with bash_exec if the user asked to run/test.\n"
            f"- Be concise. Report what changed, not what you read."
        )

    # Tasks and calendar — always call get_current_time before creating items with relative dates.
    # Without this, the model hallucinates dates (e.g. "tomorrow at 2pm" → wrong ISO timestamp).
    if intent in ("calendar", "tasks", "reminder") and active_tools:
        _time_instruction = (
            "CRITICAL: Before creating any calendar event, task, or reminder with a relative date "
            "(tomorrow, next week, in 2 days, etc.), you MUST call get_current_time() FIRST to get "
            "the exact current date and time. Use the returned ISO timestamp to calculate the target "
            "datetime. NEVER guess or invent dates. Always pass start_iso/end_iso/due_date as ISO 8601 "
            "timestamps. If the user says 'tomorrow at 2pm', call get_current_time(), add 1 day, "
            "and set the time to 14:00."
        )
        system_prompt = (system_prompt + "\n\n" + _time_instruction) if system_prompt else _time_instruction

    # Plan mode system prompt: read-only analysis
    if intent == "plan" and active_tools:
        system_prompt = (
            "You are in PLAN mode — read-only analysis.\n"
            "You have access to read_file, grep_files, find_files, list_directory, file_tree, "
            "git_status, git_diff, git_log, and run_python.\n"
            "RULES:\n"
            "- Do NOT call write_file, delete_file, bash_exec, git_commit, git_push, or any write tool.\n"
            "- Use read tools to understand the codebase first.\n"
            "- Provide analysis, not code changes.\n"
            "- If asked to modify code, explain what changes should be made instead of making them."
        )

    # Messaging: guide model through multi-platform search/send
    if intent == "messaging" and active_tools:
        system_prompt = (
            "You handle iMessage, WhatsApp, Slack, and reminders. "
            "Search for messages first before asking the user for more info. "
            "Use the platform the user specified — if unsure, state which platforms you checked. "
            "For send actions, confirm content with the user before dispatching."
        )

    # Email: guide model through the fetch → read → act pipeline
    if intent == "email" and active_tools:
        _wants_telegram = any(
            w in query.lower() for w in ("telegram", "send me", "notify me", "message me")
        )
        _wants_summary = any(
            w in query.lower() for w in ("summarize", "summarise", "summary", "tldr", "brief")
        )
        # Root cause of a live miss (2026-07-10) this used to guard with a dedicated
        # keyword-triggered branch: the model invented its own subject:(...)/after:/before:
        # filters on "do I have X today" queries, missing a same-day reminder received
        # the day before. Deleted 2026-08-02 — LIST_EMAILS_SCHEMA's description now
        # carries the same categorical "always empty query" guidance to every dispatch
        # path, verified live to hold even under this function's generic "else" prompt
        # below (schema constrains the tool call regardless of which system_prompt wins).
        if _wants_telegram:
            system_prompt = (
                "You are an email assistant. Follow these steps exactly:\n\n"
                "STEP 1: Call get_latest_email() — returns the full email body in one shot.\n"
                "STEP 2: Call send_telegram(message=<your summary>) — "
                "write a plain-text summary under 100 words (sender, subject, main point or action needed) "
                "and send it via Telegram.\n"
                "STEP 3: Confirm to the user that the summary was sent.\n\n"
                "Only 2 tool calls needed. Do not call list_emails or read_email separately."
            )
        else:
            system_prompt = (
                "You are an email assistant. Call get_latest_email() to fetch the full content of the "
                "latest email, then summarize it: sender, subject, main point or action needed (under 150 words, plain text)."
            )

    # Document automation: read file → summarize → send Telegram → create tasks
    if intent == "automation" and active_tools:
        _active_tool_names = {t["function"]["name"] for t in active_tools}
        _q_lower = query.lower()
        _wants_inbox_monitor = "run_inbox_monitor" in _active_tool_names and bool(
            re.search(r"\b(inbox|monitor|attachment)\b", _q_lower)
        )
        _wants_citations_pipeline = "read_file" in _active_tool_names and bool(
            re.search(r"\b(citation|malaria|scholar)\b", _q_lower)
        )

        if _wants_inbox_monitor:
            # Inbox PDF monitor — single tool does the full pipeline
            system_prompt = (
                "You are an automated inbox monitor assistant. "
                "When asked to check emails or run the inbox monitor, call run_inbox_monitor() immediately. "
                "Do not ask for confirmation. Do not list steps. Just call the tool. "
                "After it returns, report the result to the user."
            )
        elif _wants_citations_pipeline:
            _CITATIONS_PATH = str(Path.home() / "Downloads/scholar_malaria_citations.txt")
            system_prompt = (
                "You are a research assistant running an automated document pipeline. "
                "Follow these steps exactly:\n\n"
                f"STEP 1: Call read_file(path='{_CITATIONS_PATH}', limit=200) to load the document.\n"
                "STEP 2: Identify the 5 most recent entries (highest Year values). "
                "Note each title, authors, year, and publication.\n"
                "STEP 3: Write a concise plain-text summary under 200 words: "
                "recent topics, key trends, and any follow-up actions or research gaps.\n"
                "STEP 4: Call send_telegram(message=<your summary>).\n"
                "STEP 5: For each concrete action item (paper to read, gap to investigate, follow-up), "
                "call create_task(title=<action>, notes=<brief context>). "
                "If create_task returns a 401 or 403 auth error, call refresh_google_token() first, then retry. "
                "Skip this step entirely only if there are no action items.\n"
                "STEP 6: Confirm to the user: what was sent and how many tasks were created."
            )
        elif "create_automation" in _active_tool_names:
            # CRUD on user automations (create/list/get/update/pause/resume/delete/trigger).
            system_prompt = (
                "You manage the user's automations (scheduled/triggered workflows).\n\n"
                "IDENTIFIERS: pause_automation, resume_automation, update_automation, "
                "delete_automation, get_automation, and trigger_automation_now all accept "
                "either the automation's uuid or its exact task name — if you only have a "
                "name from earlier in the conversation, you may pass it directly. If unsure "
                "which automation the user means, call list_automations first to confirm.\n\n"
                "IF THE REQUEST DOESN'T FIT create_automation: its action_types are "
                "telegram/notification/http_webhook/email (send-a-message only, no side effects)/ "
                "tool_call (runs exactly one browser-tagged connector tool, e.g. DoorDash/Uber, and "
                "forwards its raw output)/imessage (plain send, or watch_contact+reply_message to "
                "auto-reply to every new message from one contact — no condition/filter logic). "
                "Anything beyond that (saving files, structured extraction, calendar events, "
                "multi-step/custom processing) does NOT fit it — do not write a message template "
                "that claims one of those happened. Before falling back to a partial "
                "create_automation or telling the user it's not possible, call "
                "list_available_pipelines FIRST — it lists already-built internal handlers (e.g. "
                "email.attachment_watch, inbox.monitor_attachments) that may already do exactly "
                "what's being asked. If one fits, use create_workflow with that pipeline's "
                "automation_id and the right config instead of degrading to a lesser automation. "
                "Do NOT call match_skill_for_task/list_available_skills for this — those are a "
                "different, unrelated system (interactive chat skills, not scheduled automations) "
                "and will not help here.\n\n"
                "BEFORE ASKING ANY CLARIFYING QUESTION on a create-a-workflow request: call "
                "list_available_pipelines first and check whether a builtin pipeline's own "
                "description already matches what's being asked (name, content, schedule). If so, "
                "use create_workflow with that automation_id (or tell the user it already exists) "
                "instead of asking what the content means or how it should be delivered.\n\n"
                "DUPLICATE PROTECTION IS CODE-OWNED for create_workflow: list_available_pipelines shows "
                "each pipeline's active-instance count/next-run, and create_workflow itself returns an "
                "Error naming the existing instance if one already matches (no params, or the same "
                "params) — you don't need to separately call list_automations to check for duplicates "
                "there. If create_workflow returns that error, relay it and ask whether to reuse/update "
                "the existing instance or pass force=true for a deliberate second instance.\n\n"
                "create_automation has NO equivalent hard protection — its duplicate check only warns "
                "(non-blocking) on a shared name word with the SAME trigger_type+action_type, and runs "
                "after creation. Before calling create_automation, call list_automations with no status "
                "filter (returns active AND paused) and check whether an existing automation — "
                "especially a paused one — already covers the request by trigger/action/content, not "
                "just similar wording. Prefer resume_automation (+update_automation) over creating a "
                "near-duplicate.\n\n"
                "REPORTING RESULTS: Every automation tool returns a plain-text result string. "
                "Read it before replying. If it contains 'not found', an error, or any failure "
                "wording, you MUST tell the user it failed and quote the actual tool output — "
                "never say an action 'succeeded' or was 'completed' unless the tool result "
                "explicitly confirms it. Do not paraphrase a failure as a success."
            )

    # Browser automation: step-by-step guidance for multi-turn web interaction
    if intent == "browser" and active_tools:
        system_prompt = (
            "You are a browser automation agent. You control a Chromium browser "
            "using tool calls.\n\n"
            "CRITICAL — TOOL NAMES: You MUST only use these exact tool names. "
            "NEVER invent or compose new tool names. Valid tools:\n"
            "  browser_navigate, browser_snapshot, browser_get_url, browser_click,\n"
            "  browser_type, browser_check, browser_select, browser_wait,\n"
            "  browser_get_content, browser_get_attribute, browser_run_js,\n"
            "  browser_screenshot\n"
            "If you want to click something, use browser_click(selector='...'). "
            "If you want to run JavaScript, use browser_run_js(script='...'). "
            "There is no other way — do not create function names like 'click:button:X'.\n\n"
            "SESSION MANAGEMENT: Use browser_save_session(name) to persist cookies after logging in. "
            "Use browser_load_session(name) to restore a saved session before navigating. "
            "Use session_login(service) to log into DoorDash/Uber interactively.\n\n"
            "DISCOVERY: Always call browser_snapshot() after navigate or click to see the "
            "current page structure before deciding the next action. "
            "Never guess selectors — read them from the snapshot.\n\n"
            "SPA / JS-heavy sites: If checkboxes or buttons don't respond to browser_click(), "
            "use browser_run_js() with jQuery ($ is available if the site uses it). "
            "Example: browser_run_js(script=\"$('.selectAll').prop('checked',true).trigger('change')\"). "
            "After JS-triggered AJAX actions, call browser_wait() to let the page settle.\n\n"
            "FORMS: Fill all fields first with browser_type(), then click submit. "
            "For dropdowns use browser_select(). For checkboxes use browser_check() or browser_run_js().\n\n"
            "NAVIGATION: After a form submit that triggers a redirect, call browser_get_url() "
            "to confirm where you landed before taking the next action.\n\n"
            "ERRORS: If a selector fails, use browser_snapshot() to find the correct one. "
            "Never retry the same failing selector — read the page first.\n\n"
            "PERSISTENCE: Never call browser_close() unless the user asks. "
            "Keep the browser open throughout the entire task. "
            "Complete ALL steps before reporting back."
        )

    # Local-agent: filesystem read/write operations via natural language.
    # Model-specific prompts because gemma4 (temp=1.0) needs stronger tool-call enforcement
    # while gemma4 (temp=0 on tool rounds) is already deterministic.
    if intent == "filesystem" and active_tools:
        _home = str(Path.home())
        if routed_model == ollama_client.DEFAULT_MODEL:
            system_prompt = (
                "You are local-agent running on this computer. "
                "For EVERY file operation request you MUST call a function — "
                "never describe bash commands or suggest the user run anything manually.\n\n"
                "READ TOOLS (use these to inspect files):\n"
                f"  read_document(path, pages='1-10', max_chars=12000) → read PDFs, DOCX, XLSX, images, text, configs, and code\n"
                f"  read_file(path, offset=1, limit=200)  → read lines offset..offset+limit of a file\n"
                f"  grep_files(pattern, path, glob)        → search file contents by regex; returns file:line matches\n"
                f"  find_files(pattern, path)              → locate files by name glob (e.g. '*.py')\n"
                f"  list_directory(path)                   → list contents of a folder with sizes\n"
                f"  file_tree(path, max_depth=3)           → recursive project layout\n"
                f"  read_many_files(paths, limit_per_file) → read several files in one call\n"
                f"  file_info(path)                        → size, type, modified date\n"
                f"  find_largest(path, n=5)                → biggest files in a directory\n\n"
                "WRITE TOOLS:\n"
                f"  rename_file(src, dest)        → rename or move\n"
                f"  delete_file(path, recursive)  → delete file or folder\n"
                f"  create_directory(path)        → create a new folder\n"
                f"  copy_file(src, dest)          → copy file or folder\n\n"
                "READING STRATEGY — follow this order:\n"
                "0. If the user asks to read a document or you are not sure it is plain text: call read_document(path).\n"
                "1. If you need to find something INSIDE a file: call grep_files first to get the exact line number, "
                "then call read_file(path, offset=<line>, limit=60) to show only that section.\n"
                "2. If the user asks to read a whole file: call read_file(path). "
                "If the result says 'next: offset=N', call read_file(path, offset=N) to get the next page.\n"
                "3. If you need to compare or summarize several files: use read_many_files([...]).\n"
                "4. Never read a whole large file when the user only needs one function or section.\n\n"
                "CRITICAL: Call the tool for every request — never narrate or describe what you would do. "
                f"Expand ~ to {_home}. "
                "For WRITE operations (rename/delete/create/copy): confirm in one sentence after the tool call. "
                "For READ operations: return the file content directly with no wrapper text."
            )
        else:
            system_prompt = (
                "You are local-agent, a file and folder assistant that operates on this computer. "
                "You have tools to read, list, search, find, rename, move, copy, create, and delete "
                "files and directories.\n\n"
                "Rules:\n"
                "- Always call the right tool immediately — never ask the user to do it manually.\n"
                "- For 'read this document' or unknown file types: call read_document(path='X').\n"
                "- For 'what is the biggest file in X': call find_largest(path='X', n=5).\n"
                "- For 'list files in X': call list_directory(path='X').\n"
                "- For 'rename A to B': call rename_file(src='A', dest='B').\n"
                "- For 'delete X': call delete_file(path='X'). "
                "Only pass recursive=True for non-empty directories.\n"
                "- For 'create folder X': call create_directory(path='X').\n"
                "- For 'copy A to B': call copy_file(src='A', dest='B').\n"
                f"- Expand ~ paths — the home directory is {_home}.\n"
                "- After a write operation (rename, delete, create, copy), confirm what was done in one sentence.\n"
                "- If a path does not exist or an operation is blocked, explain why and suggest a fix.\n"
                "- Never narrate what you are about to do — just call the tool, then report the result."
            )

    # Temporal / web-search / factual_qa: instruct model to use search results, not training memory
    if (intent.startswith("temporal") or intent in ("recent", "factual_qa", "web_search")) and active_tools:
        system_prompt = (
            "You have access to web_search. "
            "Your training data has a cutoff of approximately late 2024. "
            "For ANY question about schedules, upcoming events, prices, versions, recent news, "
            "recent results, match scores, standings, tournament winners, legal rulings, "
            "merger decisions, space missions, court cases, or announcements from 2025 or later: "
            "call web_search FIRST, then base your answer ONLY on what the search results say. "
            "NEVER answer from memory for events after late 2024. "
            "CRITICAL date-checking rule: if a search result is from 2023, 2024, or early 2025, "
            "it may describe events that have since concluded or changed. "
            "Before citing a source, check its date. For ongoing events (legal cases, space missions, "
            "trade disputes), prefer results from 2025 or 2026. Old articles may describe the beginning "
            "of a case or mission, not its current status. "
            f"Query formulation: include 'upcoming', 'next', or 'latest' and the current year ({_now.year}) "
            "in your search query so results are relevant and fresh. "
            "When responding after a search: answer directly and concisely. "
            "Do not narrate your reasoning, do not say 'let me check' or 'the search results show' — "
            "just state the answer as if you already know it."
        )
    elif intent in ("ocr", "vision") and active_tools:
        system_prompt = (
            "You are a multimodal assistant equipped with system tools. "
            "To see the user's screen or analyze their display, you MUST call the `take_screenshot` tool first. "
            "Never say you cannot take screenshots or access the display. "
            "Call `take_screenshot` immediately when asked to screenshot, see, read, or check the screen."
        )

    # Inject current date/time/timezone into every system prompt — taken from the
    # user's computer clock so all date calculations use the correct local timezone.
    from tools._time_context import now_line
    _dt_line = (
        now_line(_now)
        + " ALWAYS use this timezone when creating calendar events, tasks, or reminders."
    )
    # Base instruction for all chat queries: answer factual public-record questions directly.
    # Prevents small models from refusing questions about politicians, public figures, or news events.
    _base_instruction = (
        "Answer factual questions directly. Keep responses concise and to the point by default, but provide complete details and code blocks when specifically requested. "
        "Answer factual questions about public figures, politicians, and news events directly. "
        "These are public-record topics — do not refuse or hedge on them. "
        "Your training data cutoff is approximately late 2024. "
        "If you do not know the answer or your training data may be outdated, say so plainly."
    )
    if _wants_inline_svg(query):
        _svg_instruction = (
            "When the user asks for a visual, generate valid inline SVG wrapped in a fenced code block "
            "using ```svg ... ```. Prefer a single self-contained SVG with a viewBox, simple shapes, and inline text. "
            "Do not use JavaScript, external assets, CSS files, foreignObject, iframe, audio, or animation. "
            "Keep the SVG readable and polished. Add at most one short intro sentence before the code block only if needed."
        )
        if system_prompt:
            system_prompt = system_prompt + "\n\n" + _svg_instruction
        else:
            system_prompt = _svg_instruction
    if system_prompt:
        system_prompt = _dt_line + "\n\n" + system_prompt
    else:
        system_prompt = _dt_line + "\n\n" + _base_instruction
    if extra_system_prompt:
        system_prompt = system_prompt + "\n\n" + extra_system_prompt

    # Persistent cross-session memory: recall durable facts relevant to this query and
    # prepend them so they apply on every turn, all intents (returns "" when empty).
    _mem_block = memory_recall(query)
    if _mem_block:
        system_prompt = _mem_block + "\n" + system_prompt
    _activity_block = recent_activity_block()
    if _activity_block:
        system_prompt = _activity_block + "\n" + system_prompt

    # Specialist findings (data/filesystem specialists below) get folded in here instead
    # of being returned raw — the main agent (LLM + tools + history + memory) always
    # produces the final reply, referencing this context. Empty when no specialist ran.
    _specialist_context = ""

    # Per-request trace id — same shared store (store/trace_store.py) the LangGraph
    # local-agent already writes to, so this flat-loop path gets an inspectable
    # tool-call trace too (see clients.ollama_client.chat/clients.cloud_client.chat).
    # Reuse the outer run_id: subagent callers (orchestrator_tools._run_subagent)
    # pass one in and read the trace back by that id after the run.
    _run_id = run_id

    # --- Read history under lock so no concurrent thread sees stale state ---
    history = []
    if chat_id is not None:
        lock = get_lock(chat_id)
        with lock:
            raw_history = conv_get(chat_id)
            history = trim_to_budget(raw_history, routed_model, query, chat_id=chat_id)

    # Data-file analysis → data specialist, ungated by intent. "analyze sales.csv" classifies
    # as analysis/math/casual (not filesystem), so without this it never reaches the specialist
    # on chat UI / Telegram. The local-agent path already dispatches unconditionally; this gives
    # harness parity. Guarded on an actual data file + an analysis verb to avoid hijacking chat.
    _early_data = re.search(r"(\S+\.(?:csv|tsv|json|parquet|xlsx|feather|arrow))", query, re.I)
    if _early_data and re.search(
        # stems, no trailing \b so "outliers"/"correlation"/"summarize" also match
        r"\b(analy[sz]|statistic|stats?|plot|chart|graph|regress|correlat|compar|"
        r"describe|summar|outlier|pca|forecast|survival|meta.?analys|distribut|trend)",
        query, re.I,
    ):
        _resolved = os.path.expanduser(_early_data.group(1))
        if os.path.isfile(_resolved):
            _log.info("[data-specialist/early] intent=%s file=%s", intent, _resolved)
            from agents.specialists.data_specialist import run_data_specialist
            _dr = run_data_specialist(query, known_path=_resolved)
            _rendered = _render_dispatch_result(type("DR", (), {"result": _dr, "specialist": "data"})())
            # Don't return the specialist's raw output — fold it into context and let
            # the main agent (below) synthesize the reply that references it.
            _specialist_context = _rendered

    # Local-agent fast path: obvious filesystem requests should not depend on
    # model tool-call behavior. Ambiguous requests still fall through to LLM tools.
    if intent == "filesystem":
        _fs_action = parse_local_fs_action(query)
        if _fs_action is not None:
            # Check if user wants analysis (not just a raw listing)
            _wants_analysis = re.search(
                r"\b(tell|analyze|count|how many|what type|summary|report|breakdown)\b", query, re.I
            )
            # The direct path resolves to a single folder — a query naming two or more
            # storage locations (e.g. "Downloads and Google Drive") would silently drop
            # every location but the first, so defer those to the agent instead.
            _multi_location = len(set(re.findall(
                r"\b(downloads?|documents?|desktop|google drive|icloud|dropbox|onedrive)\b",
                query, re.I,
            ))) > 1
            if _wants_analysis or _multi_location:
                # Analysis requested, or multiple locations named — fall through to LangGraph agent
                _log.info(
                    "[local-agent] analysis=%s multi_location=%s, skipping direct path",
                    bool(_wants_analysis), _multi_location,
                )
            else:
                _log.info(
                    "[local-agent/direct] tool=%s write=%s args=%s",
                    _fs_action.tool_name,
                    _fs_action.is_write,
                    _fs_action.arguments,
                )
                result = execute_local_fs_action(_fs_action)
                if on_token:
                    on_token(result)
                if chat_id is not None:
                    with lock:
                        conv_append(chat_id, "user", query)
                        conv_append(chat_id, "assistant", result)
                        compact_old_turns(chat_id, routed_model)
                if tts:
                    _speak(result, voice=tts_voice)
                return result, routed_model, intent

        # Specialist dispatch: route to dedicated sub-agent if patterns match.
        # (Data-file analysis is handled earlier, ungated by intent — see [data-specialist/early].)
        _log.info("[local-agent/specialist] dispatching query=%s", query[:50])
        try:
            _dr = _specialist_dispatch(query, model=routed_model, specialist_hint=specialist_hint)
            if _dr is not None:
                _log.info("[local-agent/specialist] matched=%s", _dr.specialist)
                # Don't return the specialist's raw output — fold it into context and let
                # the LangGraph local-agent below (the main agent for this intent)
                # synthesize the reply that references it.
                _specialist_context = _render_dispatch_result(_dr)
        except Exception:
            _log.warning("[local-agent/specialist] dispatch failed, falling through", exc_info=True)

        # LangGraph local-agent: handles ambiguous filesystem requests, and always
        # produces the final reply — including when a specialist already ran above.
        _log.info("[local-agent/graph] intent=filesystem query=%s", query[:50])
        _agent_query = (
            f"{query}\n\n[Specialist findings:\n{_specialist_context}]"
            if _specialist_context else query
        )
        try:
            # ponytail: widen the classifier's input to the last user turn too —
            # a bare "read this pdf" follow-up has no document keyword itself,
            # but the turn before it usually does (confirmed live: this was
            # silently capping document-shaped tasks at the 15-step default
            # instead of the 25-step document budget).
            _history_hint = ""
            if chat_id is not None:
                _prior_turns = [t for t in conv_get(chat_id) if t.get("role") == "user"]
                if _prior_turns:
                    _history_hint = _prior_turns[-1].get("content", "")
            _agent_task = _infer_local_agent_task(f"{_history_hint} {query}")
            result = run_local_agent(
                query=_agent_query,
                model=routed_model,
                chat_id=chat_id,
                stream_callback=on_token,
                task=_agent_task,
            )
            if on_token:
                on_token(result)
            if chat_id is not None:
                with lock:
                    conv_append(chat_id, "user", query)
                    conv_append(chat_id, "assistant", result)
                    compact_old_turns(chat_id, routed_model)
            if tts:
                _speak(result, voice=tts_voice)
            return result, "local-agent-graph", intent
        except Exception as e:
            from langgraph.errors import GraphRecursionError
            from store import trace_store as _trace_store
            if isinstance(e, GraphRecursionError):
                # Known, handled fallback path — one line, not a traceback that
                # reads like a crash in the logs (confirmed live: triggered a
                # false "Crash detected" alert for a non-fatal recovery).
                _log.warning("[local-agent/graph] recursion cap hit (task=%s), falling back to LLM loop", _agent_task)
            else:
                _log.error("[local-agent/graph] failed: %s, falling back to LLM loop", e, exc_info=True)
            if _run_id:
                _trace_store.record(_run_id, {"event": "local_agent_fallback", "reason": type(e).__name__})
            # Fall through to standard LLM tool loop below

    # Code intents: route directly to local agent with code-optimized toolset.
    # Task="code" gives coding system prompt + code-only tool set.
    if intent in ("code_quick", "code_medium", "code_heavy"):
        if on_token:
            on_token("")
        _agent_query = (
            f"{query}\n\n[Specialist findings:\n{_specialist_context}]"
            if _specialist_context else query
        )
        try:
            result = run_local_agent(
                query=_agent_query,
                model=routed_model,
                chat_id=chat_id,
                stream_callback=on_token,
                task="code",
            )
            if on_token:
                on_token(result)
            if chat_id is not None:
                with lock:
                    conv_append(chat_id, "user", query)
                    conv_append(chat_id, "assistant", result)
                    compact_old_turns(chat_id, routed_model)
            if tts:
                _speak(result, voice=tts_voice)
            return result, "local-agent-graph", intent
        except Exception as e:
            _log.error("[local-agent/code] failed: %s, falling back to LLM loop", e, exc_info=True)

    # Search graph owns the whole temporal/web-search answer path.
    # When tools + model are both explicitly set (e.g. skill dispatch), skip search
    # graph entirely — the LLM tool loop handles the query with the given tools.
    if tools and model:
        _log.info("[skill-dispatch] tools=%s model=%s — skipping search graph, using LLM tool loop", tools, model)
    elif intent in ("temporal", "web_search", "factual_qa") or intent.startswith("temporal_"):
        # Conversational follow-up resolution. This branch is otherwise context-blind — it
        # hands the raw message to the web pipeline and never reads `history`, so a meta
        # follow-up ("why did you give me false info?") gets searched literally. Resolve it:
        #  - meta/self-referential → answer from history via the chat model, skip the web
        #  - context-dependent info → rewrite into a standalone search query using history
        query_for_search = query
        if history:
            from tools.followup import classify_followup
            _kind, _resolved_q = classify_followup(query, history)
            if _kind == "chat":
                _log.info("[followup] meta follow-up → chat path (was intent=%s)", intent)
                result = local_chat(
                    user_message=query,
                    # Never strictly toolless — this is answering from history, not doing
                    # filesystem work, so it doesn't need the full registry, but it should
                    # still be able to recall/store a fact if the follow-up calls for it.
                    tools=_tool("remember", "forget"),
                    model=routed_model,
                    history=history,
                    on_token=on_token,
                    system_prompt=(
                        "Answer the user's follow-up using the conversation history. "
                        "They are referring to something you said earlier — address it "
                        "directly and honestly. Do not claim you lack context."
                    ),
                    max_rounds=2,
                    options=_get_optimized_opts(intent, routed_model),
                )
                if chat_id is not None:
                    with lock:
                        conv_append(chat_id, "user", query)
                        conv_append(chat_id, "assistant", result)
                        compact_old_turns(chat_id, routed_model)
                if tts:
                    _speak(result, voice=tts_voice)
                return result, "chat-followup", intent
            if _kind == "search":
                _log.info("[followup] contextual rewrite: %r → %r", query, _resolved_q)
                query_for_search = _resolved_q

        # Strip save-to-file clauses so the search only sees the core query
        import re as _re_strip

        search_query = query_for_search
        search_query = _re_strip.sub(r"\s+to\s+(?:desktop|file|spreadsheet|excel|xlsx|\.md|\.xlsx|\.csv)\b.*$", "", search_query, flags=_re_strip.I)
        search_query = _re_strip.sub(r"\s+as\s+\.?\w+(?:\s+and\s+\.?\w+)*\s*$", "", search_query, flags=_re_strip.I)
        search_query = _re_strip.sub(
            r"\s+(?:and\s+)?(?:save|write|export|put|store)\s+(?:it|them|the\s+results?)\s*(?:to\s+(?:desktop|file|spreadsheet|excel))?\s*(?:as\s+\.?\w+)?\s*$",
            "", search_query, flags=_re_strip.I,
        )
        search_query = search_query.strip()
        if not search_query or len(search_query) < 10:
            search_query = query_for_search

        # Live sports scores: snippet search returns stale/contradictory scorelines.
        # Try a direct live source first; fall through to web search on no hit.
        result = None
        from tools.livescore import is_live_score_query as _is_live, lookup as _live_lookup
        if _is_live(query):
            result = _live_lookup(query)
            if result and on_token:
                on_token(result)
        if not result:
            result = _run_websearch(query=search_query, on_token=on_token, run_id=run_id)

        # Post-search file save
        if result and len(result) > 50:
            _wants_md = ".md" in query.lower()
            _wants_xlsx = any(x in query.lower() for x in [".xlsx", "excel", "spreadsheet"])

            if _wants_md or _wants_xlsx:
                import re as _re_fn
                from tools.shell import write_file as _write_file

                slug = _re_fn.sub(r"[^a-z0-9]+", "_", query.lower().strip())[:40].strip("_") or "results"
                desktop = str(Path.home() / "Desktop")

                if _wants_md:
                    md_path = f"{desktop}/{slug}.md"
                    try:
                        _write_file(path=md_path, content=result)
                        _log.info("[post-search-save] wrote .md to %s", md_path)
                    except Exception as e:
                        _log.error("[post-search-save] .md save failed: %s", e)

                if _wants_xlsx and len(result) >= 150 and "not found" not in result.lower():
                    try:
                        import ollama as _ollama
                        import re as _re_json
                        from tools.document_create import data_to_xlsx as _data_to_xlsx

                        resp = _ollama.chat(
                            model=routed_model,
                            messages=[{
                                "role": "user",
                                "content": (
                                    "Convert the search results below into a JSON list of objects. "
                                    "Output ONLY valid JSON array starting with [ and ending with ]. "
                                    "Each object should have key-value pairs representing the structured "
                                    "data found (e.g. name, value, rank, category, etc.).\n\n"
                                    f"{result[:3000]}"
                                ),
                            }],
                            options={"temperature": 0.0, "num_ctx": 4096, "num_predict": 1024},
                            stream=False,
                        )
                        json_result = resp.message.content or ""
                        json_match = _re_json.search(r"\[[\s\S]*\]", json_result)
                        if json_match:
                            json_path = f"{desktop}/{slug}.json"
                            xlsx_path = f"{desktop}/{slug}.xlsx"
                            _write_file(path=json_path, content=json_match.group(0))
                            _data_to_xlsx(data_path=json_path, output_path=xlsx_path)
                            _log.info("[post-search-save] wrote xlsx to %s", xlsx_path)
                    except Exception as e:
                        _log.error("[post-search-save] xlsx save failed: %s", e)

        if chat_id is not None:
            with lock:
                conv_append(chat_id, "user", query)
                conv_append(chat_id, "assistant", result)
                compact_old_turns(chat_id, routed_model)

        if tts:
            _speak(result, voice=tts_voice)

        return result, "websearch", intent

    # Fast URL fetch: bypass LLM tool loop entirely.
    # Extract URL, fetch directly, summarize with hot qwen3.5:4b — no cold model load.
    if intent == "url_fetch":
        import re as _re
        from tools.fetch_url import execute as _fetch_url
        from tools.search_result import SearchSnippet
        from tools.search_agentic import summarize_with_model as _summarize

        _url_match = _re.search(r"https?://\S+|www\.\S+\.\S+", query)
        if _url_match:
            _url = _url_match.group(0).rstrip(".,;)\"'")
            _fetched = _fetch_url(_url)
            _snip = SearchSnippet(
                title="Page content",
                url=_url,
                snippet=_fetched[:1500],
                published_date="",
                source="fetch_url",
            )
            result = _summarize(
                query,
                [_snip],
                intent="url_fetch",
                domain="general",
                timeout_s=12.0,
                on_token=on_token,
            )
            if not result:
                result = _fetched[:600]
            if chat_id is not None:
                with lock:
                    conv_append(chat_id, "user", query)
                    conv_append(chat_id, "assistant", result)
            if tts:
                _speak(result, voice=tts_voice)
            # Reset plan mode flag after completion
            import tools.registry as _reg

            _reg.PLAN_MODE_ACTIVE = False
            return result, routed_model, intent

    # LLM call is outside the lock — it's slow and chat-independent
    _max_rounds = (
        max_rounds
        if max_rounds is not None
        else (
            20
            if intent == "browser"
            else (30 if (_ide_override or intent == "automation") else MAX_ROUNDS)
        )
    )
    # Voice-originated subagents inherit CURRENT_CHAT_ID (read-only) from the
    # orchestrator's context; stateless subagents never re-set it. Cap their rounds.
    if max_rounds is None and CURRENT_CHAT_ID.get(None) == "brabble_voice":
        _max_rounds = min(_max_rounds, _VOICE_MAX_ROUNDS)
    _optimized_opts = _get_optimized_opts(intent, routed_model)

    # Safety net: a specialist may have run above (data-specialist's ungated early
    # dispatch, or the filesystem specialist when run_local_agent then raised) without
    # its context ever reaching a synthesis step yet — fold it in here too.
    if _specialist_context:
        system_prompt = f"[Specialist findings:\n{_specialist_context}]\n\n{system_prompt}"

    # Auth self-heal: any Google-touching intent should recover from a stale/scope-missing
    # OAuth token within the same turn instead of just reporting failure. Without this, a
    # 401/403 gets written into conversation history as "I can't access X", and on the next
    # user message in the same chat the model pattern-matches that prior failure and refuses
    # to even retry — inventing a permanent-incapability excuse instead of calling
    # refresh_google_token() (found 2026-07: stale-scope 403 on Google Tasks). Placed last,
    # after every intent-specific system_prompt assignment above (email's block fully
    # overwrites system_prompt rather than appending to it, so this must run after it).
    if active_tools and any(t["function"]["name"] == "refresh_google_token" for t in active_tools):
        _auth_retry_line = (
            "If any Google tool call in this turn returns a 401, 403, or authentication/"
            "permission error: call refresh_google_token() immediately, then retry the failed "
            "tool once before reporting failure. A past failure in the conversation history "
            "does not mean the capability is permanently broken — always retry fresh."
        )
        system_prompt = (system_prompt + "\n\n" + _auth_retry_line) if system_prompt else _auth_retry_line

    # Same delete-hallucination risk one level down: once inside the automation
    # subagent's own tool loop, force the actual delete call too. Always targets
    # delete_automation (the only reversible, always-safe option) — deciding
    # "soft vs. a real permanent delete" by keyword-matching the query was tried
    # and reverted: the forced-tool shortcut in cloud_client._run_tool_loop only
    # knows how to invoke query-only tools, so it called delete_automation/
    # delete_workflow_permanently with a bogus {"query": ...} arg, failed schema
    # validation every time, and contributed nothing (confirmed via trace_entries
    # 2026-07-12) — the honest "this is only a soft delete" response the model
    # gives already comes from delete_automation's own tool description, not
    # from tool selection. Whether to escalate to a real permanent delete is a
    # judgment call for the model to make from that description, not a regex.
    _sub_forced_tool = (
        "delete_automation"
        if intent == "automation" and _DELETE_AUTOMATION_RE.search(query)
        else None
    )
    result = local_chat(
        user_message=query,
        tools=active_tools,
        model=routed_model,
        history=history,
        on_token=on_token,
        system_prompt=system_prompt,
        max_rounds=_max_rounds,
        images=images or None,
        options=_optimized_opts,
        run_id=_run_id,
        force_tool_choice=_sub_forced_tool,
        reasoning_effort=reasoning_effort_for_intent(intent),
        round_timeout=round_timeout,
    )
    result = _normalize_svg_reply(result)

    # --- Write turns + compact under the same per-chat lock ---
    if chat_id is not None:
        with lock:
            conv_append(chat_id, "user", query)
            conv_append(chat_id, "assistant", result)
            compact_old_turns(chat_id, routed_model)

    if tts:
        _speak(result, voice=tts_voice)

    return result, routed_model, intent


def run_for_messaging(*args, **kwargs):
    """run(), gated by _MESSAGING_GATE so Telegram + WhatsApp firing at once don't
    stack concurrent Ollama generations on one GPU. Web UI callers use run() directly."""
    with _MESSAGING_GATE:
        return run(*args, **kwargs)


# Extracted to harness_tts.py / harness_audio.py (self-contained pieces, peeled off
# this file's dispatch-chain monolith) — re-exported here so existing callers
# (harness._speak, harness.run_audio, etc.) don't need to change their import path.
from harness_tts import _speak, _strip_md, _MD_STRIP_RE  # noqa: F401
from harness_audio import run_audio  # noqa: F401


if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "What year is it and what's new in AI?"
    print(run(query, tts=False))
