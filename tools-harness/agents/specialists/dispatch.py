"""
Rule-based specialist dispatch router — 9 specialists, zero-cost classification.

Priority chain (first match wins): form → video → audio → scraper → data → write → research → read → path → main agent

Each specialist has accept patterns and negation patterns. Negation wins over accept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agents.specialists.path_specialist import PathResult, run_path_specialist
from agents.specialists.read_specialist import ReadResult, run_read_specialist
from agents.specialists.data_specialist import DataResult, run_data_specialist
from agents.specialists.form_specialist import FormResult, run_form_specialist
from agents.specialists.write_specialist import WriteResult, run_write_specialist
from agents.specialists.scraper_specialist import ScraperResult, run_scraper_specialist
from agents.specialists.research_specialist import ResearchResult, run_research_specialist
from agents.specialists.audio_specialist import AudioResult, run_audio_specialist
from agents.specialists.video_specialist import VideoResult, run_video_specialist
from agents.specialists.transport_specialist import TransportResult, run_transport_specialist


# ---------------------------------------------------------------------------
# Pattern definitions per specialist
# ---------------------------------------------------------------------------

def _pat(regex: str) -> re.Pattern:
    return re.compile(regex, re.IGNORECASE)

_SPEC = {
    "form": {
        "accept": [
            _pat(r"\bfill\b.{0,30}\b(?:form|pdf|docx|doc)\b"),
            _pat(r"\bfill\s+(?:out|in)\b"),
            _pat(r"\bcomplete\b.{0,30}\b(?:form|pdf)\b"),
            _pat(r"\bdetect\b.{0,20}\b(?:form|field)\b"),
        ],
        "negate": [
            _pat(r"\bread|write|create|generate|transcribe|download\b"),
            _pat(r"\bfill\s+up\b"),
        ],
    },
    "video": {
        "accept": [
            _pat(r"\b(?:download|get|save|grab|rip|fetch)\b.{0,40}\b(?:video|youtube|yt|clip|tiktok|vimeo)\b"),
            _pat(r"\byoutu(?:be\.com|\.be)\b|youtu\.be"),
            _pat(r"\b(?:trim|cut|convert|resize|compress)\b.{0,30}\b(?:video|mp4|mkv|mov|avi|gif)\b"),
            _pat(r"\bextract\s+(?:audio|sound|mp3)\b"),
            _pat(r"\b(?:video|movie|clip|footage)\b.{0,30}\b(?:download|process|edit|info|details)\b"),
            _pat(r"\byt-dlp|ffmpeg\b"),
        ],
        "negate": [
            _pat(r"\byoutube\s+(?:search|transcript|music)\b"),
        ],
    },
    "audio": {
        "accept": [
            _pat(r"\btranscribe\b"),
            _pat(r"\bspeech\s+to\s+text\b"),
            _pat(r"\b(?:convert|extract)\s+audio\s+to\s+text\b"),
            _pat(r"\bwhisper\b"),
            _pat(r"\b(?:audio|voice|recording|sound)\b.{0,30}\b(?:transcrib|to\s+text)\b"),
            # Audio format conversion: "convert X.wav to mp3", "convert recording.m4a to flac"
            _pat(r"\bconvert\b.{0,60}\.(?:m4a|mp3|wav|flac|ogg|aac|opus)\b"),
            _pat(r"\.(?:m4a|wav|flac|ogg|aac|opus)\b.{0,30}\bto\s+(?:mp3|m4a|wav|flac|ogg|aac|opus)\b"),
        ],
        "negate": [
            # Only block "download audio" / "process video" — not format conversion
            _pat(r"\b(?:download|process|trim)\s+(?:audio|video)\b"),
        ],
    },
    "scraper": {
        "accept": [
            _pat(r"\b(?:scrape|scrap|crawl|crawling|extract)\b"),
            _pat(r"\b(?:browse|navigate|open\s+in\s+browser)\b"),
            _pat(r"\bscreenshot\b.{0,20}\b(?:website|webpage|site|url|page)\b"),
            _pat(r"\b(?:grab|pull|get)\b.{0,20}\b(?:from\s+the\s+web|website|url|page)\b"),
        ],
        "negate": [
            _pat(r"\b(?:find|search|locate)\b.{0,20}\b(?:file|folder|directory)\b"),
        ],
    },
    "data": {
        "accept": [
            _pat(r"\banaly[sz]e?\b.{0,30}\b(?:data|dataset|csv|table|column|spreadsheet|excel|json|parquet)\b"),
            _pat(r"\b(?:plot|chart|graph|visualize|histogram|correlation|statistics?|stats?)\b"),
            _pat(r"\b(?:describe|summarize|profile|analy[sz]e)\b.{0,40}\b(?:dataset|data|csv|table|trends?|patterns?|insights?|statistic|columns?|variables?)\b"),
            _pat(r"\bsql\b.{0,20}\b(?:query|on|against)\b"),
            _pat(r"\b(?:pivot|groupby|aggregate|filter)\b.{0,20}\b(?:data|by)\b"),
            _pat(r"\b(?:mean|median|std|stddev|percentile)\b.{0,20}\b(?:of|for|calculate)\b"),
            # Additional practical phrasings
            _pat(r"\b(?:clean|wrangle|tidy|preprocess|transform)\b.{0,20}\b(?:data|csv|dataset|table)\b"),
            _pat(r"\b(?:compare|contrast)\b.{0,30}\b(?:group|arm|cohort|treatment|condition)\b"),
            _pat(r"\b(?:correlation|relationship)\b.{0,20}\b(?:between|of)\b"),
            _pat(r"\b(?:regression|linear\s*model|logistic|glm|cox|survival|kaplan.meier|meta.analysis|forest\s*plot|funnel)\b"),
            _pat(r"\b(?:time\s*series|forecast|arima|acf|pacf|seasonal|decomposition)\b"),
            _pat(r"\b(?:pca|principal\s*component|factor\s*analysis|clustering|kmeans)\b"),
            _pat(r"\b(?:t.test|anova|chi.square|mann.whitney|kruskal|wilcoxon)\b"),
            _pat(r"\b(?:outlier|anomaly|detect|flag)\b.{0,20}\b(?:data|value|point)\b"),
            _pat(r"\bexplore\b.{0,20}\b(?:data|dataset|csv|table)\b"),
            _pat(r"\b(?:trend|pattern|insight)\b.{0,20}\b(?:data|in)\b"),
            _pat(r"\b(?:run|do|perform)\b.{0,10}\b(?:stats?|statistics?|analysis)\b"),
        ],
        "negate": [
            _pat(r"\b(?:read|open|view|show|display)\s+(?:the\s+)?(?:file|pdf|doc)\b"),
        ],
    },
    "write": {
        "accept": [
            _pat(r"\b(?:create|make|write|generate|build|produce|craft)\b.{0,30}\b(?:file|script|document|spreadsheet|slides?|presentation|report|note|markdown|html|csv|json|yaml|toml|ini|cfg|text)\b"),
            _pat(r"\b(?:edit|modify|update|change|fix|patch)\b.{0,20}\b(?:file|code|script|document|config)\b"),
            _pat(r"\b(?:save|output|export|convert\s+to)\b.{0,20}\b(?:file|docx|pdf|xlsx|pptx|csv)\b"),
            _pat(r"\bnew\s+(?:file|directory|folder|script|document)\b"),
        ],
        "negate": [
            _pat(r"\b(?:read|view|open|show|display)\b.{0,20}\bthe\b"),
            _pat(r"\b(?:transcribe|fill|scrape|download|analy[sz]e)\b"),
        ],
    },
    "research": {
        "accept": [
            _pat(r"\b(?:pubmed|medline|pmid)\b"),
            _pat(r"\b(?:arxiv|preprint)\b"),
            _pat(r"\b(?:research|scholar|academic|scientific|literature|paper|journal)\b.{0,30}\b(?:search|find|look\s+up)\b"),
            _pat(r"\b(?:papers?\s+(?:about|on|regarding)|find\s+(?:research|papers?))\b"),
            _pat(r"\bsemantic\s+scholar\b"),
            _pat(r"\b(?:evidence|study|trial)\b.{0,30}\b(?:for|on|about)\b"),
        ],
        "negate": [
            _pat(r"\b(?:youtube|video|download|transcribe)\b"),
        ],
    },
    "read": {
        "accept": [
            _pat(r"\b(?:read|open|view|show|display|look\s+at|check)\b.{0,30}\b(?:file|document|pdf|text|code|script|csv|json|yaml)\b"),
            _pat(r"\bread\s+(?:the\s+)?(?:file|pdf|doc|document|text)"),
            _pat(r"\bwhat(?:\'s| is)\s+in\s+(?:this|that|the)\s+(?:file|pdf|doc|document)\b"),
            _pat(r"\b(?:show|display)\s+(?:me\s+)?(?:the\s+)?(?:contents?|file|text)\b"),
        ],
        "negate": [
            _pat(r"\b(?:write|create|edit|fill|transcribe|analy[sz]e|download)\b"),
        ],
    },
    "transport": {
        "accept": [
            _pat(r"\buber\b"), _pat(r"\blyft\b"), _pat(r"\btaxi\b"),
            _pat(r"\bbus\b"), _pat(r"\bsubway\b"), _pat(r"\bm b t a\b"),
            _pat(r"\btransit\b"),
            _pat(r"\bdirections?\s+(?:from|to|between)\b"),
            _pat(r"\b(?:ride|price|cost|estimate|fare|eta|pickup|dropoff)\b.{0,40}\b(?:uber|lyft|taxi|bus|transit)\b"),
            _pat(r"\b(?:uber|lyft|taxi|bus|transit)\b.{0,40}\b(?:price|cost|estimate|fare|eta|pickup|dropoff|from|to)\b"),
        ],
        "negate": [
            _pat(r"\b(?:stock|share|ipo|revenue|profit|earnings|valuation|market|investor)\b"),
            _pat(r"\b(?:transit|transfer)\b.{0,30}\b(?:file|data|money|payment|fund|wire)\b"),
        ],
    },
    "path": {
        "accept": [
            _pat(r"\b(?:find|locate|search|look\s*for|look\s*up|discover)\b.{0,60}\b(?:file|folder|directory|audio|pdf|doc|docx?|txt|py|json|csv|image|video|m4a|mp3|path|location)\b"),
            _pat(r"\b(?:list|ls|show)(?:\s+(?:the|all))?\s+(?:files?|contents?|director(?:y|ies)|folders?)\b"),
            _pat(r"\b(?:where\s+is|what('s|\s+is)\s+in|contents?\s+of)\b"),
            _pat(r"\b(?:grep|search\s+for)\b"),
        ],
        "negate": [
            _pat(r"\b(?:create|write|make|new|generate|build|code|develop)\b.{0,30}\b(?:file|script|program|function|app)\b"),
            _pat(r"\b(?:transcribe|translate|summari[sz]e|read|fill|edit|update)\b"),
        ],
    },
}

# Priority chain: form → video → audio → transport → scraper → data → write → research → read → path
_PRIORITY = ["form", "video", "audio", "transport", "scraper", "data", "write", "research", "read", "path"]


def classify(query: str) -> str | None:
    """Return the specialist name that matches, or None."""
    for name in _PRIORITY:
        spec = _SPEC[name]
        # Negation check first
        negated = any(p.search(query) for p in spec["negate"])
        if negated:
            continue
        # Accept check
        if any(p.search(query) for p in spec["accept"]):
            return name
    return None


# ---------------------------------------------------------------------------
# Dispatchers
# ---------------------------------------------------------------------------

def _mk_events(name: str, query: str, **kwargs) -> dict:
    return {
        "start": ("specialist_start", {"name": name, "query": query}),
        "done": ("specialist_done", {"name": name, **kwargs}),
    }


def _dispatch_path(query: str, model: str):
    r = run_path_specialist(query, model=model)
    return r, _mk_events("path_discovery", query, paths=r.paths, summary=r.summary,
                         tool_trace=r.tool_trace, elapsed_s=r.elapsed_s, error=r.error)

def _dispatch_read(query: str, model: str, known_path: str | None = None):
    r = run_read_specialist(query, model=model, known_path=known_path)
    return r, _mk_events("read", query, text=r.text[:200], path=r.path, tool_used=r.tool_used,
                         tool_trace=r.tool_trace, elapsed_s=r.elapsed_s, error=r.error)

def _dispatch_data(query: str, model: str, known_path: str | None = None):
    r = run_data_specialist(query, model=model, known_path=known_path)
    return r, _mk_events("data_analysis", query, text=r.text[:300], chart_path=r.chart_path,
                         tool_trace=r.tool_trace, elapsed_s=r.elapsed_s, error=r.error)

def _dispatch_form(query: str, model: str, known_path: str | None = None):
    r = run_form_specialist(query, model=model, known_path=known_path)
    return r, _mk_events("form", query, pdf_path=r.pdf_path, fields_detected=r.fields_detected,
                         verified=r.verified, tool_trace=r.tool_trace, elapsed_s=r.elapsed_s, error=r.error)

def _dispatch_write(query: str, model: str):
    r = run_write_specialist(query, model=model)
    return r, _mk_events("write", query, paths=r.paths, tool_trace=r.tool_trace,
                         elapsed_s=r.elapsed_s, error=r.error)

def _dispatch_scraper(query: str, model: str):
    r = run_scraper_specialist(query, model=model)
    return r, _mk_events("scraper", query, content=r.content[:500], url=r.url,
                         screenshot=r.screenshot_path, tool_trace=r.tool_trace,
                         elapsed_s=r.elapsed_s, error=r.error)

def _dispatch_research(query: str, model: str):
    from tools.deep_research import execute as deep_research_execute
    import time
    t0 = time.time()
    try:
        report = deep_research_execute(query, depth=2, breadth=3)
        r = ResearchResult(
            text=report,
            sources=[],
            tool_trace=["deep_research"],
            elapsed_s=time.time() - t0,
        )
    except Exception as e:
        r = ResearchResult(
            text=f"Deep research failed: {e}",
            sources=[],
            tool_trace=["deep_research"],
            elapsed_s=time.time() - t0,
            error=str(e),
        )
    return r, _mk_events("research", query, text=r.text[:500],
                         tool_trace=r.tool_trace, elapsed_s=r.elapsed_s, error=r.error)

def _dispatch_audio(query: str, model: str, known_path: str | None = None):
    r = run_audio_specialist(query, model=model, known_path=known_path)
    return r, _mk_events("audio", query, transcript=r.transcript[:500], path=r.path,
                         tool_trace=r.tool_trace, elapsed_s=r.elapsed_s, error=r.error)

def _dispatch_video(query: str, model: str):
    r = run_video_specialist(query, model=model)
    return r, _mk_events("video", query, file_path=r.file_path,
                         transcript=r.transcript[:500], tool_trace=r.tool_trace,
                         elapsed_s=r.elapsed_s, error=r.error)


def _dispatch_transport(query: str, model: str):
    r = run_transport_specialist(query, model=model)
    return r, _mk_events("transport", query, text=r.text[:200],
                         services=r.services, elapsed_s=r.elapsed_s, error=r.error)


_DISPATCHERS = {
    "form": _dispatch_form,
    "video": _dispatch_video,
    "audio": _dispatch_audio,
    "transport": _dispatch_transport,
    "scraper": _dispatch_scraper,
    "data": _dispatch_data,
    "write": _dispatch_write,
    "research": _dispatch_research,
    "read": _dispatch_read,
    "path": _dispatch_path,
}


# ---------------------------------------------------------------------------
# Top-level dispatch entry point
# ---------------------------------------------------------------------------

@dataclass
class DispatchResult:
    specialist: str
    result: object
    events: dict


def dispatch(
    query: str,
    model: str = "gemma4:12b-mlx",
    known_path: str | None = None,
    specialist_hint: str | None = None,
) -> DispatchResult | None:
    """
    Classify the query and dispatch to the right specialist.
    Returns DispatchResult or None if no specialist matches.

    `specialist_hint`: when the caller already ran clients.router.classify_message()
    and got a specialist name back, pass it here to skip this module's own regex
    classify() entirely. Falls back to classify() if the hint isn't a valid name.
    """
    name = specialist_hint if specialist_hint in _PRIORITY else classify(query)
    if not name:
        return None

    # ponytail: specialists call ollama.chat() directly (no cloud_client fallback,
    # they're a local-only fast ReAct loop) — a cloud-routed model string
    # (e.g. "deepseek/deepseek-v4-flash", passed through unchanged since
    # cloud-first routing landed) 404s against local Ollama since no local
    # model has that name. Clamp to the local default here, the one place
    # every specialist call funnels through, instead of patching all 8 files.
    from clients import cloud_client, ollama_client
    if cloud_client.is_cloud_model(model):
        model = ollama_client.DEFAULT_MODEL

    fn = _DISPATCHERS[name]
    # Some specialists accept known_path
    import inspect
    sig = inspect.signature(fn)
    if "known_path" in sig.parameters:
        result, events = fn(query, model=model, known_path=known_path)
    else:
        result, events = fn(query, model=model)

    return DispatchResult(specialist=name, result=result, events=events)


# Backward-compatible exports from original dispatch.py
def should_dispatch_to_path(query: str) -> bool:
    return classify(query) == "path"

def dispatch_path_specialist(query: str, model: str = "gemma4:12b-mlx"):
    r = run_path_specialist(query, model=model)
    return r, _mk_events("path_discovery", query, paths=r.paths, summary=r.summary,
                         tool_trace=r.tool_trace, elapsed_s=r.elapsed_s, error=r.error)
