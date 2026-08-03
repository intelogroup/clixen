"""
Message router — classifies a user message and returns the best local model.

Active fleet (2026-04-27):
┌────────────────┬────────┬──────────┬────────┬────────────────────────────────────┐
│ Model          │ Params │ Ctx (tok)│ Tools  │ Strengths                          │
├────────────────┼────────┼──────────┼────────┼────────────────────────────────────┤
│ gemma4         │  9.6B  │  128 K   │ native │ Primary chat, tools, vision, OCR    │
│ qwen3:8b       │   8B   │  128 K   │ native │ Agentic multi-step pipelines        │
└────────────────┴────────┴──────────┴────────┴────────────────────────────────────┘

Routing (2026-07): all intents → CLOUD_MODEL (DeepSeek direct API primary,
Claude Haiku 4.5 via OpenRouter fallback — see clients/cloud_client.py for
why) by default — gemma4 was proving unreliable past 2-3 chained tool calls.
gemma4 stays reachable via manual model override; dial specific low-stakes
intents back to it in TASK_ROUTING/_INTENT_MODEL_OVERRIDES once the cloud
path is proven. The intent classifier (_llm_classify/warm_classifier) now
also runs on CLOUD_MODEL — gemma4 was too slow (26s cold, busting 5s timeout),
so classification stays fast (~1-2s) via cloud API like everything else.

KNOWN DUPLICATION (2026-07, investigated for dedup, deliberately left as-is):
classify() and classify_telegram() share most of their ~20 intent branches
but in DIFFERENT RELATIVE ORDER in several places — e.g. _LOCAL_DISCOVERY_RE
runs early in classify() (before filesystem) but late in classify_telegram()
(after casual/code/calendar/tasks/email); classify_telegram() also has two
branches classify() lacks entirely ("casual chat < 35 chars", "document"
intent). A mechanical merge into one shared ordered table can't preserve both
orderings without becoming an equally complex per-channel-order-override
mechanism, or silently changing one channel's routing to match the other's —
exactly the regression this duplication note exists to prevent. If attempting
a merge later: build the golden-test comparison FIRST (representative sample
message per intent per channel, assert old vs. new output identical) and
expect to find real order-dependent divergences, not just the "shared table
+ per-channel defaults" shape this used to (wrongly) assume.
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from clients.cloud_client import DEFAULT_CLOUD_MODEL as CLOUD_MODEL

_log = logging.getLogger(__name__)

# (context_window_tokens, reserved_for_response_tokens)
# Context windows verified via `ollama show` on 2026-04-14.
MODEL_SPECS: dict[str, tuple[int, int]] = {
    "gemma4:12b-mlx": (16384, 4096),
    "qwen3:8b": (16384, 2048),
    "gemma4:e2b": (16384, 4096),
    "qwen3:4b": (16384, 2048),
    "qwen3.5:4b": (16384, 2048),
    # Cloud models — published provider context windows, not independently
    # verified like the local `ollama show` figures above.
    "deepseek/deepseek-v4-flash": (128_000, 8192),
    "openrouter/anthropic/claude-haiku-4.5": (200_000, 8192),
    "openrouter/google/gemini-3.1-flash-lite": (1_000_000, 8192),
}


# Rough token estimate: 1 token ≈ 4 chars

# Pure regex constants + order-independent helpers split out for size;
# see clients/router_patterns.py. classify()/classify_telegram()/classify_ide()
# below are untouched and must not be reordered (see module docstring above).
from clients import router_patterns as _rp
globals().update({k: v for k, v in vars(_rp).items() if not k.startswith("__")})

def classify(message: str) -> tuple[str, str]:
    """
    Returns (model_name, intent) for the given message.

    Intents: casual | code_quick | code_medium | code_heavy | math | analysis | general | temporal

    Web chat uses CLOUD_MODEL for everything by default (see module docstring);
    gemma4 remains available via manual override.
    """
    msg = message.strip()
    length = len(msg)

    # Conversation-memory questions ("what's my name", "what did I tell you") stay on the
    # chat model with history — must beat browser/temporal/factual routing.
    if _CONVO_REF_RE.search(msg):
        return CLOUD_MODEL, "casual"

    # Browser automation → CLOUD_MODEL (warm, native tools). A local file path/extension
    # ("fill out the form in resume.docx") means it's a filesystem form-fill request,
    # not web-form automation — let it fall through to _FILESYSTEM_RE below.
    if _BROWSER_RE.search(msg) and not _LOCAL_FILE_RE.search(msg):
        return CLOUD_MODEL, "browser"

    # OCR → dedicated cloud vision model
    if _OCR_RE.search(msg):
        return "openrouter/google/gemini-2.5-flash", "ocr"  # dedicated cloud vision model for OCR intent

    # YouTube search/transcript → dedicated tools (search_youtube, get_youtube_transcript).
    # Checked early, before _BROWSER_RE's bare "search"/generic web patterns could steal it.
    if _YOUTUBE_RE.search(msg):
        return CLOUD_MODEL, "youtube"

    # Chinese-internet search → agent-reach tooling (Bilibili / Exa / XiaoHongShu / Weibo).
    if _CHINESE_WEB_RE.search(msg):
        return CLOUD_MODEL, "chinese_web"

    # Library / framework docs → CLOUD_MODEL (warm, sufficient for docs Q&A). Guarded against
    # _is_temporal: the bare product-name list (openai, anthropic, stripe, ...) otherwise
    # steals current-events questions that merely mention one ("who's the current CEO of
    # OpenAI") into an ungrounded chat answer instead of grounded web search. Also guarded
    # against _ANALYSIS_RE: a bare framework mention inside a conceptual question ("explain
    # the tradeoffs between REST and GraphQL") isn't a syntax/API lookup — found 2026-07-02.
    if _LIBRARY_DOCS_RE.search(msg) and not _is_temporal(msg) and not _ANALYSIS_RE.search(msg):
        return CLOUD_MODEL, "library_docs"

    # Git operations → CLOUD_MODEL (warm, native tools)
    if _GIT_RE.search(msg):
        return CLOUD_MODEL, "git"

    # Python REPL / code execution → CLOUD_MODEL (warm)
    if _REPL_RE.search(msg):
        return CLOUD_MODEL, "repl"

    # Local discovery (restaurant/business/place lookup with live criteria) → search graph.
    # Must come before _FILESYSTEM_RE — "show me X" in _FILESYSTEM_RE would otherwise steal it.
    if _LOCAL_DISCOVERY_RE.search(msg):
        return CLOUD_MODEL, "temporal"

    # Filesystem / file reading queries → CLOUD_MODEL (warm, native tools). Skip when a URL is
    # present — "contents of" collides with url_fetch phrasings like "fetch and summarize
    # the contents of https://example.com" (found 2026-07-02: routed to the local-agent
    # filesystem path, which then genuinely hung trying to read a URL as a local path).
    # Also skip when _MACOS_NATIVE_RE matches — bare "what's in" collides with "what's in
    # my clipboard" (found 2026-07-02); classify_telegram() already checks macos_native
    # before filesystem, this brings classify() in line with that ordering.
    if _FILESYSTEM_RE.search(msg) and not _URL_RE.search(msg) and not _MACOS_NATIVE_RE.search(msg):
        return CLOUD_MODEL, "filesystem"

    # Hard math → CLOUD_MODEL (warm)
    if _HARD_MATH_RE.search(msg):
        return CLOUD_MODEL, "math"

    # Heavy code tasks (implement/refactor/architecture/tests) → CLOUD_MODEL (warm)
    if _CODE_HEAVY_RE.search(msg):
        return CLOUD_MODEL, "code_heavy"

    # Automation / workflows / watchers → CLOUD_MODEL (tool calls, handles automation schemas)
    # Must be checked before _CODE_QUICK_RE (which matches "list") and before _TASKS_RE.
    if _AUTOMATION_RE.search(msg):
        return CLOUD_MODEL, "automation"

    # Native macOS / archive intents — must run BEFORE _CODE_QUICK_RE because
    # phrases like "list my safari tabs" match code_quick on the word "list".
    if _SYSTEM_STATUS_RE.search(msg):
        return CLOUD_MODEL, "system_status"
    if _MACOS_NATIVE_RE.search(msg):
        return CLOUD_MODEL, "macos_native"
    if _SPOTLIGHT_RE.search(msg):
        return CLOUD_MODEL, "spotlight"
    if _SLACK_SEARCH_RE.search(msg):
        return CLOUD_MODEL, "slack"
    if _IMESSAGE_SEARCH_RE.search(msg):
        return CLOUD_MODEL, "imessage"
    if _WHATSAPP_SEARCH_RE.search(msg):
        return CLOUD_MODEL, "whatsapp_search"

    # Code snippets → CLOUD_MODEL. Skip when it looks like filesystem ("list files"), temporal/
    # current-info ("list the top N ... in 2026"), or tasks ("add X to my task list") so those
    # aren't grabbed by the bare word "list" (found 2026-07-02 via live testing).
    if (
        _CODE_QUICK_RE.search(msg)
        and not _FILESYSTEM_RE.search(msg)
        and not _is_temporal(msg)
        and not _TASKS_RE.search(msg)
    ):
        if length < 200:
            return CLOUD_MODEL, "code_quick"
        if length < 2000:
            return CLOUD_MODEL, "code_medium"
        return CLOUD_MODEL, "code_heavy"

    # Inbox PDF monitor → CLOUD_MODEL (warm, native tools)
    if _INBOX_PDF_RE.search(msg):
        return CLOUD_MODEL, "automation"

    # Document automation: summarize + deliver via Telegram + optionally create tasks.
    # Must come before calendar/tasks/reminder so "add to tasks" sub-steps don't steal the intent.
    if _DOC_AUTOMATION_RE.search(msg) and (
        _DELIVERY_CHANNEL_RE.search(msg) or _AGENTIC_RE.search(msg) or _TASKS_RE.search(msg)
    ):
        return CLOUD_MODEL, "automation"

    # Agentic email: multi-step fetch→summarize→deliver pipelines.
    if _EMAIL_RE.search(msg) and (_AGENTIC_RE.search(msg) or _DELIVERY_CHANNEL_RE.search(msg)):
        return CLOUD_MODEL, "email"

    # Slack archive search (slacrawl) — read-only FTS over local DB
    if _SLACK_SEARCH_RE.search(msg):
        return CLOUD_MODEL, "slack"

    # iMessage / SMS archive search — read-only via ~/Library/Messages/chat.db
    if _IMESSAGE_SEARCH_RE.search(msg):
        return CLOUD_MODEL, "imessage"

    # WhatsApp archive search (bridge-written DB)
    if _WHATSAPP_SEARCH_RE.search(msg):
        return CLOUD_MODEL, "whatsapp_search"

    # Native macOS surfaces (clipboard, Safari tabs, Notes)
    if _MACOS_NATIVE_RE.search(msg):
        return CLOUD_MODEL, "macos_native"

    # Spotlight: "find the PDF about X" — checked before generic filesystem
    if _SPOTLIGHT_RE.search(msg):
        return CLOUD_MODEL, "spotlight"

    # Calendar → CLOUD_MODEL (warm, native tools)
    if _CALENDAR_RE.search(msg):
        return CLOUD_MODEL, "calendar"

    # Tasks → CLOUD_MODEL (warm, native tools)
    if _TASKS_RE.search(msg):
        return CLOUD_MODEL, "tasks"

    # Reminder → CLOUD_MODEL (warm, native tools)
    if _REMINDER_RE.search(msg):
        return CLOUD_MODEL, "reminder"

    # Simple email (read/list/send, single-tool) → CLOUD_MODEL (warm)
    if _EMAIL_RE.search(msg):
        return CLOUD_MODEL, "email"

    # "Save to desktop/file" + any search/temporal signal → force temporal (search graph)
    if _DESKTOP_SAVE_RE.search(msg) and (
        _is_temporal(msg)
        or any(
            x in msg.lower()
            for x in [
                "search",
                "find",
                "look up",
                "lookup",
                "get",
                "fetch",
            ]
        )
    ):
        return CLOUD_MODEL, "temporal"

    # Transit / transportation → CLOUD_MODEL (warm, bus_eta + estimate_uber_ride tools)
    # Must be checked before _TEMPORAL_RE (transit queries often have "cost"/"price"/"right now")
    if _TRANSIT_RE.search(msg):
        return CLOUD_MODEL, "transit"

    # Temporal / current info → CLOUD_MODEL + web_search
    if _is_temporal(msg):
        return CLOUD_MODEL, "temporal"

    # Multilingual input (Unicode script) → CLOUD_MODEL (warm, multilingual capable)
    if _MULTILINGUAL_RE.search(msg):
        return CLOUD_MODEL, "multilingual"

    # Haitian Creole / plain-ASCII French — not caught by Unicode range above
    if _CREOLE_FRENCH_RE.search(msg):
        return CLOUD_MODEL, "multilingual"

    # Bare URL or URL-centric query → CLOUD_MODEL (warm, can fetch and summarize)
    if _URL_RE.search(msg):
        return CLOUD_MODEL, "url_fetch"

    # Analysis → CLOUD_MODEL (warm, good for most analysis)
    if _ANALYSIS_RE.search(msg):
        return CLOUD_MODEL, "analysis"

    # Bare arithmetic ("2+2", "15% of 300", "what is 12*9") → local math, not a web search.
    # classify_telegram already has this; classify() was missing it, so simple math fell
    # through to factual_qa and got resolved via web search instead of direct computation.
    if _SIMPLE_MATH_RE.search(msg):
        return CLOUD_MODEL, "math"

    # Factual questions (what/who/when/where/why/how + is/are) → CLOUD_MODEL (warm)
    # Knowledge base lookup preferred, fallback to gemma4's training data
    if _FACTUAL_QUESTION_RE.search(msg):
        return CLOUD_MODEL, "factual_qa"

    # Default (casual / general Q&A) → CLOUD_MODEL (warm, fast, accurate)
    # Reserved for: greetings, opinions, conversational exchanges, general knowledge
    return CLOUD_MODEL, "casual"


# Real casual chat + generative asks that must stay on the LOCAL model — a web search
# can't fulfill them: greetings, social pleasantries, acknowledgements, emotional small-talk,
# and creative/roleplay requests (write a poem, tell a joke, brainstorm, pretend you're…).
# Everything NOT matched here and NOT matched by a task intent falls through to web-grounded
# factual_qa — per user directive, the model's unverified factual memory is not trusted.
_CASUAL_CHAT_RE = re.compile(
    r"^\s*(?:hi+|hey+|hello+|yo+|sup|howdy|hiya|greetings|"
    r"good\s+(?:morning|afternoon|evening|night)|"
    r"thanks?|thank\s+you|thx|ty|cheers|ok(?:ay)?|cool|nice|awesome|great|"
    r"lol+|lmao|haha+|hehe+|nvm|nope|yep|yup|sure|got\s+it)\b"
    r"|\b(?:how\s+are\s+you|how'?s\s+it\s+going|what'?s\s+up|how\s+have\s+you\s+been|"
    r"who\s+are\s+you|what\s+can\s+you\s+do|tell\s+me\s+about\s+yourself|"
    r"i'?m\s+(?:tired|bored|happy|sad|good|fine|back|here|excited)|"
    r"you'?re\s+(?:funny|smart|cool|awesome|the\s+best|wrong)|love\s+you|good\s+night)\b"
    r"|\b(?:write|compose|draft|create|make\s+up|come\s+up\s+with|give\s+me|"
    r"tell\s+me\s+(?:a|an|another)|sing)\b[\s\S]{0,30}"
    r"\b(?:poem|story|stories|joke|jokes|song|haiku|rhyme|riddle|essay|limerick|"
    r"tagline|slogan|caption|pickup\s+line)\b"
    r"|\b(?:brainstorm|role-?play|pretend\s+you|act\s+as|imagine\s+you'?re)\b",
    re.IGNORECASE,
)

# Bare arithmetic → local math (not web). "2+2", "15% of 300", "what is 12 * 9".
_SIMPLE_MATH_RE = re.compile(
    r"\d+\s*[-+*/×÷^%]\s*\d+"
    r"|\d+\s*%\s+of\s+\d+"
    r"|\b(?:what\s+is|whats|calculate|compute|solve)\b[\s\d.()+\-*/×÷^%\s]{0,20}\d",
    re.IGNORECASE,
)


def classify_telegram(message: str) -> tuple[str, str]:
    """
    Telegram-context router. Same intent detection as classify().
    All intents route to gemma4 (warm, native tools, 128K ctx).
    """
    msg = message.strip()
    length = len(msg)

    # Conversation-memory questions ("what's my name", "what did I tell you") stay on the
    # chat model with history — must beat browser/temporal/factual routing.
    if _CONVO_REF_RE.search(msg):
        return CLOUD_MODEL, "casual"

    # Browser automation → CLOUD_MODEL (warm, native tools). Local file path/extension
    # means filesystem form-fill, not web-form automation — defer to _FILESYSTEM_RE.
    if _BROWSER_RE.search(msg) and not _LOCAL_FILE_RE.search(msg):
        return CLOUD_MODEL, "browser"

    # OCR → dedicated cloud vision model
    if _OCR_RE.search(msg):
        return "openrouter/google/gemini-2.5-flash", "ocr"  # dedicated cloud vision model for OCR intent

    # YouTube search/transcript → dedicated tools. See classify().
    if _YOUTUBE_RE.search(msg):
        return CLOUD_MODEL, "youtube"

    # Chinese-internet search → agent-reach tooling. See classify().
    if _CHINESE_WEB_RE.search(msg):
        return CLOUD_MODEL, "chinese_web"

    # Library / framework docs → CLOUD_MODEL. Guarded against _is_temporal and _ANALYSIS_RE — see classify().
    if _LIBRARY_DOCS_RE.search(msg) and not _is_temporal(msg) and not _ANALYSIS_RE.search(msg):
        return CLOUD_MODEL, "library_docs"

    # Git operations → CLOUD_MODEL (filesystem/git now enabled on Telegram)
    if _GIT_RE.search(msg):
        return CLOUD_MODEL, "git"

    # Python REPL / code execution → CLOUD_MODEL
    if _REPL_RE.search(msg):
        return CLOUD_MODEL, "repl"

    # Hard math → CLOUD_MODEL
    if _HARD_MATH_RE.search(msg):
        return CLOUD_MODEL, "math"

    # Heavy code tasks → CLOUD_MODEL (warm)
    if _CODE_HEAVY_RE.search(msg):
        return CLOUD_MODEL, "code_heavy"

    # Automation / workflows / watchers → CLOUD_MODEL (tool calls, handles automation schemas)
    # Must be checked before _CODE_QUICK_RE (which matches "list") and before _TASKS_RE.
    if _AUTOMATION_RE.search(msg):
        return CLOUD_MODEL, "automation"

    # Document creation (pdf/docx/xlsx/pptx) → local-agent graph (handled in telegram_bot.py).
    # After automation so "create a workflow" wins; before code/filesystem so "make a pdf" lands here.
    if _DOC_CREATE_RE.search(msg):
        return CLOUD_MODEL, "document"

    # Native macOS / archive intents — must run BEFORE _CODE_QUICK_RE.
    if _SYSTEM_STATUS_RE.search(msg):
        return CLOUD_MODEL, "system_status"
    if _MACOS_NATIVE_RE.search(msg):
        return CLOUD_MODEL, "macos_native"
    if _SPOTLIGHT_RE.search(msg):
        return CLOUD_MODEL, "spotlight"
    if _SLACK_SEARCH_RE.search(msg):
        return CLOUD_MODEL, "slack"
    if _IMESSAGE_SEARCH_RE.search(msg):
        return CLOUD_MODEL, "imessage"
    if _WHATSAPP_SEARCH_RE.search(msg):
        return CLOUD_MODEL, "whatsapp_search"

    # Real casual chat / creative asks → stay on the local model (web can't do these).
    # Placed before code/calendar/email so "compose a haiku" isn't stolen by _EMAIL_RE
    # ("compose"), but after automation/doc-create so "make a pdf of a poem" still wins.
    if len(msg) < 35 and _CASUAL_CHAT_RE.search(msg):
        return CLOUD_MODEL, "casual"

    # Code snippets → CLOUD_MODEL. Skip when it looks like filesystem ("list files"), temporal/
    # current-info ("list the top N ... in 2026"), or tasks ("add X to my task list") so those
    # aren't grabbed by the bare word "list" (found 2026-07-02 via live testing).
    if (
        _CODE_QUICK_RE.search(msg)
        and not _FILESYSTEM_RE.search(msg)
        and not _is_temporal(msg)
        and not _TASKS_RE.search(msg)
    ):
        if length < 200:
            return CLOUD_MODEL, "code_quick"
        if length < 2000:
            return CLOUD_MODEL, "code_medium"
        return CLOUD_MODEL, "code_heavy"

    # Calendar → CLOUD_MODEL (warm, has tool support)
    if _CALENDAR_RE.search(msg):
        return CLOUD_MODEL, "calendar"

    # Tasks → CLOUD_MODEL (warm, has tool support)
    if _TASKS_RE.search(msg):
        return CLOUD_MODEL, "tasks"

    # Reminder → CLOUD_MODEL (warm, has tool support)
    if _REMINDER_RE.search(msg):
        return CLOUD_MODEL, "reminder"

    # Agentic email → CLOUD_MODEL (warm, native tools)
    if _EMAIL_RE.search(msg) and (_AGENTIC_RE.search(msg) or _DELIVERY_CHANNEL_RE.search(msg)):
        return CLOUD_MODEL, "email"

    # Simple email → CLOUD_MODEL (warm, native tools)
    if _EMAIL_RE.search(msg):
        return CLOUD_MODEL, "email"

    # Local discovery → search graph (same path as temporal)
    if _LOCAL_DISCOVERY_RE.search(msg):
        return CLOUD_MODEL, "temporal"

    # "Save to desktop/file" + any search/temporal signal → force temporal (search graph)
    if _DESKTOP_SAVE_RE.search(msg) and (
        _is_temporal(msg)
        or any(x in msg.lower() for x in ["search", "find", "look up", "lookup", "get", "fetch"])
    ):
        return CLOUD_MODEL, "temporal"

    # Filesystem / file reading → CLOUD_MODEL (now enabled on Telegram). Checked before temporal,
    # matching classify()'s order (line ~131 vs ~251) — filesystem wins on overlap on both
    # channels now (was inverted here: 2026-07-11 fix, see plan-a-soft-yawning-turing).
    # Skip when a URL is present — see classify().
    if _FILESYSTEM_RE.search(msg) and not _URL_RE.search(msg):
        return CLOUD_MODEL, "filesystem"

    # Temporal / current info → CLOUD_MODEL (warm, has web_search tool). Use _is_temporal, not
    # a bare _TEMPORAL_RE.search — the former also catches live-sports ("who won X vs Y")
    # and price+market cues, so those get grounded search instead of an ungrounded
    # (hallucinated) chat answer.
    # Guarded against _STRONG_LOCAL_FS_RE: "how many files in my Downloads folder right now"
    # is unambiguously a filesystem query — a bare "right now" shouldn't send it to web search.
    # Redundant with the filesystem check above for _FILESYSTEM_RE matches now that filesystem
    # runs first, but kept as a second guard in case _is_temporal fires on non-_FILESYSTEM_RE
    # local-discovery phrasing this doesn't catch.
    if _is_temporal(msg) and not _STRONG_LOCAL_FS_RE.search(msg):
        return CLOUD_MODEL, "temporal"

    # Multilingual (Unicode script or Creole/French keywords) → CLOUD_MODEL (warm, multilingual capable)
    if _MULTILINGUAL_RE.search(msg) or _CREOLE_FRENCH_RE.search(msg):
        return CLOUD_MODEL, "multilingual"

    # Bare URL → url_fetch. Matches classify()'s _URL_RE position (after temporal,
    # before analysis). classify_telegram was missing this — URLs fell through to factual_qa.
    if _URL_RE.search(msg):
        return CLOUD_MODEL, "url_fetch"

    # Analysis / explanation ("how does X work", "compare A and B") → web-grounded, NOT a
    # naked model answer. Per user directive the model's factual memory isn't trusted, so
    # explanatory questions get evidence. (Genuine paste-analysis is rare on Telegram; it
    # still gets a web-augmented answer.) ponytail: if long-paste analysis regresses, gate
    # this on message length and send short/no-paste ones to web, long ones to analysis.
    if _ANALYSIS_RE.search(msg):
        return CLOUD_MODEL, "factual_qa"

    # Bare arithmetic ("2+2", "15% of 300", "what is 12*9") → local math, not a web search.
    # Sports/temporal scorelines like "3-0" are already caught above, so this is safe here.
    if _SIMPLE_MATH_RE.search(msg):
        return CLOUD_MODEL, "math"

    # Everything else = a factual / general-knowledge question → web-grounded factual_qa.
    # This is the flipped default: unmatched messages that used to get a naked (hallucinated)
    # model answer now go through search. Real greetings/creative are caught above as casual.
    return CLOUD_MODEL, "factual_qa"


def classify_ide(query: str) -> tuple[str, str]:
    """
    IDE-aware router. Reuses classify() for intent detection.
    Uses gemma4 for everything except OCR/vision (gemma4).
    NOTE: OCR/vision uses gemma4 (multimodal) — same as all other intents.
    """
    _, intent = classify(query)

    if intent == "ocr":
        return "gemma4:12b-mlx", "ocr"  # IDE mode stays local/offline, deliberate
    elif intent == "multilingual":
        return CLOUD_MODEL, "multilingual"
    else:
        return CLOUD_MODEL, intent


# ---------------------------------------------------------------------------
# Unified classifier — single entry point for telegram_bot.py, whatsapp_bot.py,
# and harness.py's internal dispatch. Replaces four independent classification
# passes (classify_telegram → discarded intent, harness's own classify(),
# specialists/dispatch.py's own regex classify(), each re-deriving intent from
# the same message text) with ONE decision per message: an LLM call, primary,
# with the existing regex cascades kept only as the offline/timeout fallback.
# classify()/classify_telegram()/classify_ide() above are left untouched —
# they're exercised by deterministic tests with no LLM mocking, and this
# fallback needs their exact tuned behavior, not a hand-merged approximation.
# ---------------------------------------------------------------------------

_KNOWN_INTENTS = frozenset(
    {
        "casual", "code_quick", "code_medium", "code_heavy", "math", "analysis",
        "browser", "ocr", "library_docs", "git", "repl", "temporal", "filesystem",
        "automation", "system_status", "macos_native", "spotlight", "slack",
        "imessage", "whatsapp_search", "email", "calendar", "tasks", "reminder",
        "transit", "multilingual", "url_fetch", "factual_qa", "document", "youtube",
    }
)

_SPECIALIST_HINTS = frozenset(
    {"form", "video", "audio", "transport", "scraper", "data", "write", "research", "read", "path"}
)

# Deterministic model lookup keyed by intent — not an LLM output. Removes a
# hallucination surface (model can't return a bogus model name) and matches
# the fact that nearly every intent already resolves to gemma4 today.
# These 5 used to point to "mistral-nemo" (inherited from the original
# classify()/classify_telegram() regex cascades) -- that model isn't installed
# (see CLAUDE.md), so every message hitting one of these intents crashed with
# an unhandled 500 (ollama.ResponseError: model not found). Found 2026-07-01
# via live testing "how much battery do I have left on my mac". Remapped to
# gemma4:12b-mlx, same as the fallback default.
_INTENT_MODEL_OVERRIDES = {
    "slack": CLOUD_MODEL,
    "imessage": CLOUD_MODEL,
    "whatsapp_search": CLOUD_MODEL,
    "system_status": CLOUD_MODEL,
    "macos_native": CLOUD_MODEL,
}


def model_for_intent(intent: str) -> str:
    """Deterministic model lookup for a pre-computed intent — used by harness.py
    when a caller already ran classify_message() and passed `intent` directly.

    Falls back to local (ollama_client.DEFAULT_MODEL) instead of the chosen
    cloud model when today's cost_guard budget is already blown, or when
    routing_stats shows that cloud model has been failing more than half its
    last 5+ calls — both real degraded-cloud conditions, not speculative.
    """
    from clients import routing_stats

    picked = _INTENT_MODEL_OVERRIDES.get(intent, CLOUD_MODEL)
    if not picked.startswith("openrouter/") and picked != CLOUD_MODEL:
        routing_stats.record_decision(intent, picked, "pinned")
        return picked
    try:
        from clients import cost_guard
        cost_guard.check_budget()
    except cost_guard.BudgetExceededError:
        from clients.ollama_client import DEFAULT_MODEL
        routing_stats.record_decision(intent, DEFAULT_MODEL, "budget_blocked")
        return DEFAULT_MODEL
    if routing_stats.is_unhealthy(picked):
        from clients.ollama_client import DEFAULT_MODEL
        routing_stats.record_decision(intent, DEFAULT_MODEL, "unhealthy_fallback")
        return DEFAULT_MODEL
    routing_stats.record_decision(intent, picked, "normal")
    return picked


# Adaptive-thinking budget per intent, forwarded as OpenRouter's `reasoning.effort`
# (only the openrouter/ prefix supports it — see cloud_client._resolve). Cheap/social/
# lookup intents get None (skip the reasoning pass entirely, fastest+cheapest); intents
# that are genuinely multi-step or logic-heavy get "high"; everything else "low" so the
# model still gets a short scratchpad without eating latency on every casual message.
_REASONING_EFFORT: dict[str, str] = {
    "casual": None, "factual_qa": None, "library_docs": None, "browser": None,
    "filesystem": None, "system_status": None, "spotlight": None, "macos_native": None,
    "url_fetch": None, "temporal": None, "youtube": None,
    "math": "high", "code_heavy": "high", "analysis": "high", "automation": "high", "plan": "high",
}


def reasoning_effort_for_intent(intent: str) -> str | None:
    """None/low/medium/high budget for the given intent; unlisted intents default
    to "low" — a light reasoning pass rather than none, since most unlisted intents
    (email, code_medium, document, git, ...) are one step up from pure lookup."""
    return _REASONING_EFFORT.get(intent, "low")


@dataclass(frozen=True)
class Classification:
    model: str
    intent: str
    specialist_hint: str | None
    source: str  # "guard" | "llm" | "regex_fallback" — logging only, not behavior


_INTENT_TAXONOMY = """casual - greetings, social pleasantries, small talk, creative writing (poems/jokes), roleplay (never use for factual questions, even if they start with a greeting or use conversational phrasing like "do you know")
code_quick / code_medium / code_heavy - writing, fixing, or explaining code; pick by how much work is implied
math - arithmetic, calculus, proofs, statistics
analysis - explain/compare/summarize a topic, literature review
browser - navigate to specific URLs, log into accounts (Uber/DoorDash/etc), fill web forms, or scrape web page content (never use for general search/QA queries)
ocr - read text from an image/screenshot, or take a screenshot
library_docs - API/syntax question about a specific library or framework
git - git status/diff/commit/branch operations
repl - run or execute a snippet of code and show the output
temporal - needs current/fresh info: weather, news, prices, scores, "what's happening now", place/business lookups
filesystem - read/list/find/move/rename/delete local files or folders
automation - create/manage a recurring workflow, watcher, or scheduled job
system_status - battery, disk space, memory, wifi status of this Mac
macos_native - clipboard, Safari tabs, Apple Notes
spotlight - find a specific file/photo/screenshot by name or recent time window
slack - search or read Slack messages
imessage - search or read iMessage/SMS history
whatsapp_search - search WhatsApp message history
email - read, search, or send email
calendar - view or create calendar events
tasks - view or manage a to-do list
reminder - set a one-off reminder
transit - Uber/Lyft/bus/directions
multilingual - message is in a non-English language
url_fetch - message contains a URL to fetch/summarize
factual_qa - general knowledge, history, geography, science, or factual questions, including requests to search the web or google, even if they start with a greeting or use conversational phrasings like "Do you know...", "Can you tell me..." (e.g. "Do you know who played...", "Can you tell me the last time...")
document - generate a PDF/DOCX/XLSX/PPTX/Markdown file from content
youtube - search YouTube for videos, or get a transcript from a YouTube video/URL"""


def _llm_classify(message: str, channel: str) -> "Classification | None":
    """One CLOUD_MODEL call (fast, ~1-2s). Returns None — triggering the
    regex fallback — on any exception, timeout, unparseable output, or
    an intent outside the known set."""
    from clients.cloud_client import chat as cloud_chat

    prompt = (
        "Classify this message into exactly one intent.\n\n"
        f"Intents:\n{_INTENT_TAXONOMY}\n\n"
        'If intent is "filesystem", also set specialist_hint to one of: '
        "form, video, audio, transport, scraper, data, write, research, read, path, or null.\n"
        "Otherwise specialist_hint is null.\n\n"
        f"Message: {message}\n"
        'Output ONLY JSON: {"intent": "...", "specialist_hint": null}'
    )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            raw = pool.submit(
                lambda: cloud_chat(prompt, tools=[], max_rounds=1)
            ).result(timeout=10.0)
        raw = raw.strip()
        start = raw.find('{')
        end = raw.rfind('}')
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end+1]
        else:
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        data = json.loads(raw)
        intent = data.get("intent")
        if intent not in _KNOWN_INTENTS:
            return None
        if intent == "document" and channel not in ("telegram", "whatsapp"):
            return None
        hint = data.get("specialist_hint")
        if hint not in _SPECIALIST_HINTS:
            hint = None
        return Classification(model=model_for_intent(intent), intent=intent, specialist_hint=hint, source="llm")
    except Exception as e:
        _log.warning("classify_message LLM stage failed, falling back to regex: %s", e)
        return None


def warm_classifier() -> None:
    """Warm cloud endpoint for _llm_classify() — one cheap call so subsequent
    classifies don't pay cold-start latency."""
    from clients.cloud_client import chat as cloud_chat
    try:
        cloud_chat(
            'Output ONLY JSON: {"intent": "casual", "specialist_hint": null}',
            tools=[], max_rounds=1,
        )
        _log.info("classifier warmup ok for CLOUD_MODEL")
    except Exception as e:
        _log.warning("classifier warmup failed for CLOUD_MODEL: %s", e)


def _classify_regex_fallback(message: str, channel: str) -> Classification:
    """Safety net when the LLM stage is unavailable — delegates straight to
    the existing, already-tuned classify()/classify_telegram() cascades (never
    a single point of failure: Ollama down → exact today's regex-only
    behavior, not an error)."""
    if channel in ("telegram", "whatsapp"):
        model, intent = classify_telegram(message)
    else:
        model, intent = classify(message)
    return Classification(model=model, intent=intent, specialist_hint=None, source="regex_fallback")


def classify_message(message: str, channel: str = "web", *, history: list | None = None) -> Classification:
    """
    Single entry point for telegram_bot.py, whatsapp_bot.py, and harness.py's
    internal dispatch — replaces each of them independently re-classifying
    the same message text. `channel` in {"telegram","whatsapp","web","ide"}
    gates "document" intent eligibility and which regex fallback path is used.
    `history` is accepted but unused here — history-dependent disambiguation
    (meta-followup vs. contextual rewrite) stays in tools/followup.py, called
    only after intent has already resolved to a search-shaped one.
    """
    msg = message.strip()

    # Stage 0 — fast-path: only purely syntactic guards (character-level, not
    # language-level). The LLM handles all semantic intent classification at
    # Stage 1. No regex-based language guards — they decay, collide with proper
    # nouns, and steal queries from smarter classification.
    if _SIMPLE_MATH_RE.search(msg):
        return Classification(CLOUD_MODEL, "math", None, "guard")
    if _YOUTUBE_RE.search(msg):
        return Classification(CLOUD_MODEL, "youtube", None, "guard")
    if _CHINESE_WEB_RE.search(msg):
        return Classification(CLOUD_MODEL, "chinese_web", None, "guard")

    # Stage 1 — primary: one LLM call.
    result = _llm_classify(msg, channel)
    if result is not None:
        return result

    # Stage 2 — fallback: regex cascade.
    return _classify_regex_fallback(msg, channel)


def classify_plan(query: str) -> tuple[str, str]:
    """
    Plan-mode router — read-only analysis mode.
    Always uses gemma4 for speed, always returns 'plan' intent.
    """
    return CLOUD_MODEL, "plan"


def available_history_tokens(model: str) -> int:
    """
    How many tokens of history fit for this model while leaving room
    for the system prompt (~300 tok), current user message, and response budget.
    """
    ctx, reserved = MODEL_SPECS.get(model, (32768, 1024))
    system_overhead = 300
    return ctx - reserved - system_overhead


# ---------------------------------------------------------------------------
# Agent task routing — static config for the worker dispatcher.
# These tasks bypass classify() — the model and tools are known at definition
# time, so no runtime inference is needed.
# ---------------------------------------------------------------------------

TASK_ROUTING: dict[str, dict] = {
    "morning_briefing": {
        "model": CLOUD_MODEL,  # Agentic pipeline: fetch calendar/tasks/emails
        "tools": [
            "list_calendar_events",
            "list_tasks",
            "list_emails",
            "send_telegram",
            "get_current_time",
        ],
        "max_rounds": 5,
        "system_prompt_key": "automation",
    },
    "email_pipeline": {
        "model": CLOUD_MODEL,
        "tools": [
            "list_pdf_attachments",
            "convert_pdf",
            "list_emails",
            "read_email",
            "create_task",
            "get_current_time",
            "refresh_google_token",
            "read_file",
            "ask_fetch_url",
        ],
        "max_rounds": 8,
        "system_prompt_key": "automation",
    },
    "doc_summarizer": {
        "model": CLOUD_MODEL,
        "tools": [
            "read_file",
            "send_telegram",
            "create_task",
            "get_current_time",
            "refresh_google_token",
        ],
        "max_rounds": 10,
        "system_prompt_key": "automation",
    },
    "price_tracker": {
        "model": CLOUD_MODEL,
        "tools": ["web_search"],
        "max_rounds": 5,
        "system_prompt_key": "temporal",
    },
    "inbox_autopilot": {
        "model": CLOUD_MODEL,
        "tools": [
            "list_emails",
            "read_email",
            "create_task",
            "send_telegram",
            "get_current_time",
        ],
        "max_rounds": 6,
        "system_prompt_key": "automation",
    },
    "calendar_copilot": {
        "model": CLOUD_MODEL,
        "tools": [
            "list_calendar_events",
            "create_calendar_event",
            "list_tasks",
            "send_telegram",
            "get_current_time",
        ],
        "max_rounds": 6,
        "system_prompt_key": "automation",
    },
    "admin_cleanup": {
        "model": CLOUD_MODEL,
        "tools": [
            "list_tasks",
            "create_task",
            "list_emails",
            "send_telegram",
            "get_current_time",
        ],
        "max_rounds": 6,
        "system_prompt_key": "automation",
    },
    "watch_and_alert": {
        "model": CLOUD_MODEL,
        "tools": [
            "web_search",
            "send_telegram",
            "get_current_time",
        ],
        "max_rounds": 5,
        "system_prompt_key": "temporal",
    },
    "browser_chore": {
        "model": CLOUD_MODEL,
        "tools": [
            "browser_navigate",
            "browser_snapshot",
            "browser_click",
            "browser_type",
            "browser_wait",
            "browser_close",
            "send_telegram",
        ],
        "max_rounds": 8,
        "system_prompt_key": "automation",
    },
    "family_logistics": {
        "model": CLOUD_MODEL,
        "tools": [
            "list_calendar_events",
            "list_tasks",
            "create_task",
            "send_telegram",
            "get_current_time",
        ],
        "max_rounds": 6,
        "system_prompt_key": "automation",
    },
    "founder_pipeline": {
        "model": CLOUD_MODEL,
        "tools": [
            "list_emails",
            "list_tasks",
            "web_search",
            "send_telegram",
            "create_task",
            "get_current_time",
        ],
        "max_rounds": 7,
        "system_prompt_key": "automation",
    },
    "knowledge_to_action": {
        "model": CLOUD_MODEL,
        "tools": [
            "read_file",
            "list_tasks",
            "create_task",
            "send_telegram",
            "get_current_time",
        ],
        "max_rounds": 7,
        "system_prompt_key": "automation",
    },
    "travel_mode": {
        "model": CLOUD_MODEL,
        "tools": [
            "list_calendar_events",
            "set_reminder",
            "send_telegram",
            "get_current_time",
            "web_search",
        ],
        "max_rounds": 6,
        "system_prompt_key": "automation",
    },
    "stubborn_reminder": {
        "model": CLOUD_MODEL,
        "tools": [
            "set_reminder",
            "create_task",
            "send_telegram",
            "get_current_time",
        ],
        "max_rounds": 5,
        "system_prompt_key": "automation",
    },
    "tech_brief": {
        "model": "qwen3.5:4b",
        "tools": [
            "browseros_navigate",
            "browseros_snap",
            "send_email",
        ],
        "max_rounds": 5,
        "system_prompt_key": "automation",
    },
    # ponytail: stub entry — worker.py's _execute_job() guard checks TASK_ROUTING
    # before its _TASK_MODULES dispatch branch, which handles agent_message entirely
    # via jobs.agent_message_job (model/tools/etc. below are never read for this task).
    "agent_message": {
        "model": CLOUD_MODEL,
        "tools": [],
        "max_rounds": 1,
        "system_prompt_key": "automation",
    },
}
