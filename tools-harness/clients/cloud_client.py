"""
Cloud chat client — counterpart to ollama_client.py.

Mirrors ollama_client.chat()'s signature and tool-loop shape (build messages ->
loop up to max_rounds -> execute tool calls via tools.registry.execute_tool ->
append tool-result messages) but talks to a cloud OpenAI-compatible endpoint
instead of a local Ollama server. Two providers are supported, picked by
model-string prefix so callers (harness.py, local_agent_nodes.py) dispatch on
a plain string without a separate provider field:
  - "openrouter/<provider>/<model>" -> OpenRouter (OPENROUTER_API_KEY)
  - "deepseek/<model>"              -> DeepSeek's own API (DEEPSEEK_API_KEY)
    (added because this account's OpenRouter key is provider-restricted and
    can't reach DeepSeek through OpenRouter at all — see below)

Image/vision support: chat()'s `images` param (raw base64, ollama's convention)
works when routed to a vision-capable model — verified 2026-07-05 against
CLOUD_VISION_MODEL (google/gemini-2.5-flash-lite via OpenRouter). DEFAULT_CLOUD_MODEL
(deepseek/deepseek-v4-flash) and CLOUD_FALLBACK_MODEL are text-only tool-calling
models; only harness.py's vision-intent routing should pass images, pointed at
CLOUD_VISION_MODEL specifically.
"""

import base64
import contextvars
import json
import logging
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeoutError
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

from dotenv import load_dotenv
from openai import OpenAI

from clients.cancellation import check_aborted, QueryAbortedException
from clients.cost_guard import check_budget, record_usage, BudgetExceededError  # noqa: F401 (re-exported)
from store.trace_store import record as _record_trace

# harness.py already loads tools-harness/.env for the main entrypoints, but
# standalone callers (benchmark/e2e scripts, ad-hoc debugging) import this
# module directly without going through harness.py first — load it here too
# so OPENROUTER_API_KEY/DEEPSEEK_API_KEY are never silently missing.
# python-dotenv doesn't override already-exported shell env vars by default,
# so this is safe alongside a real env var of the same name.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from tools.registry import execute_tool, is_error_result, is_checkpoint_result
from tools.injection_guard import wrap_external_output

_log = logging.getLogger(__name__)

# ponytail: single same-model retry w/ jittered backoff for transient errors (rate
# limit, timeout, connection drop, 5xx) before the existing cross-provider fallback
# in chat() kicks in. Raise _TRANSIENT_MAX_RETRIES if these still surface to users often.
_TRANSIENT_MAX_RETRIES = 1
_TRANSIENT_BASE_DELAY = 1.0


def _create_with_retry(client, **kwargs):
    from openai import APIConnectionError, APITimeoutError, RateLimitError, InternalServerError

    for attempt in range(_TRANSIENT_MAX_RETRIES + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError) as e:
            if attempt >= _TRANSIENT_MAX_RETRIES:
                raise
            delay = _TRANSIENT_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
            _log.warning("[cloud_client] transient error (%s), retrying in %.1fs", e, delay)
            time.sleep(delay)


def _create_stream_with_retry(client, **kwargs):
    from openai import APIConnectionError, APITimeoutError, RateLimitError, InternalServerError

    for attempt in range(_TRANSIENT_MAX_RETRIES + 1):
        try:
            return client.chat.completions.create(
                stream=True, stream_options={"include_usage": True}, **kwargs
            )
        except (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError) as e:
            if attempt >= _TRANSIENT_MAX_RETRIES:
                raise
            delay = _TRANSIENT_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
            _log.warning("[cloud_client] transient error (%s), retrying in %.1fs", e, delay)
            time.sleep(delay)


def _reasoning_extra_body(model: str, reasoning_effort: str | None) -> dict:
    """OpenRouter's unified `reasoning.effort` knob (low/medium/high) — adaptive
    thinking depth per call. Only openrouter/-prefixed models are verified to accept
    it (Claude extended-thinking passthrough); DeepSeek's direct API 400s on an
    unknown field, so silently drop there rather than crash the call."""
    if not reasoning_effort or not model.startswith("openrouter/"):
        return {}
    return {"extra_body": {"reasoning": {"effort": reasoning_effort}}}


def _is_deepseek_thinking(real_model: str) -> bool:
    """DeepSeek's thinking models (v4-flash, reasoner) are hostile to forced tool calls:
    native `tool_choice` -> 400 "Thinking mode does not support this tool_choice", and a
    *fabricated* assistant tool-call turn -> 400 "reasoning_content ... must be passed
    back". The round-0 pre-seed is the only forcing path that avoids tool_choice — it
    just needs a `reasoning_content` on the synthetic assistant turn to satisfy the
    second constraint. ponytail: substring-gated on observed thinking models; widen if
    a new one appears.
    """
    m = real_model.lower()
    return "deepseek" in m and ("v4" in m or "reasoner" in m)


# Longest _LEAK_TOKEN_RE alternative is ~64 chars (the <｜...｜> forms with a
# {0,60} body) — buffer this many trailing chars before emitting so a marker
# split across two SSE chunks still gets caught. See _StreamLeakFilter.
_LEAK_HOLDBACK = 80


class _StreamLeakFilter:
    """Buffers streamed deltas so a leak marker split across multiple chunks
    (e.g. "<｜｜DSML" in one delta, "｜｜tool_calls>" in the next) is caught
    before reaching on_token. _strip_leak_artifacts alone isn't enough here:
    it only runs on the fully-aggregated content after the stream ends, but
    on_token fires live, per-chunk — confirmed live 2026-07-10 ("should I
    bring an umbrella" streamed raw <｜｜DSML｜｜tool_calls> tokens straight to
    the user before the end-of-message scrub ever ran). Holds back the last
    _LEAK_HOLDBACK chars, only flushing the safe prefix each feed(); flush()
    drains what's left at stream end. Uses the non-stripping regex directly
    (not _strip_leak_artifacts, which .strip()s — that would eat spaces
    between chunk boundaries)."""

    def __init__(self, on_token: Optional[Callable[[str], None]]):
        self._on_token = on_token
        self._buf = ""

    def feed(self, text: str) -> None:
        self._buf += text
        safe_len = len(self._buf) - _LEAK_HOLDBACK
        if safe_len <= 0:
            return
        cleaned = _LEAK_TOKEN_RE.sub("", self._buf[:safe_len])
        if cleaned and self._on_token:
            self._on_token(cleaned)
        self._buf = self._buf[safe_len:]

    def flush(self) -> None:
        cleaned = _LEAK_TOKEN_RE.sub("", self._buf)
        if cleaned and self._on_token:
            self._on_token(cleaned)
        self._buf = ""


def _stream_completion(client, on_token: Optional[Callable[[str], None]], **kwargs):
    """Stream a chat completion, firing on_token(delta) per content chunk as it
    arrives instead of buffering the whole reply server-side and calling
    on_token once at the end. Returns a SimpleNamespace shaped like a
    non-streaming response (.choices[0].message.{content,tool_calls}, .usage)
    so the rest of _run_tool_loop's parsing doesn't need to change."""
    stream = _create_stream_with_retry(client, **kwargs)
    content_parts: list[str] = []
    tool_call_acc: dict[int, dict] = {}
    usage = None
    finish_reason = None
    leak_filter = _StreamLeakFilter(on_token)
    for chunk in stream:
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        if not chunk.choices:
            continue
        # 2026-07-11: streaming APIs set finish_reason ("stop"/"length"/"tool_calls")
        # only on the terminal chunk — this reconstruction dropped it entirely, so
        # a response silently cut short by the API's own token ceiling (no max_tokens
        # is passed anywhere in this module) was indistinguishable from a genuinely
        # complete answer. Confirmed live: a mid-word cutoff ("...a hundred perc")
        # with a clean, exception-free round completion in the logs.
        if getattr(chunk.choices[0], "finish_reason", None):
            finish_reason = chunk.choices[0].finish_reason
        delta = chunk.choices[0].delta
        if delta.content:
            content_parts.append(delta.content)
            leak_filter.feed(delta.content)
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                acc = tool_call_acc.setdefault(
                    tc_delta.index, {"id": "", "name": "", "arguments": ""}
                )
                if tc_delta.id:
                    acc["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        acc["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        acc["arguments"] += tc_delta.function.arguments

    leak_filter.flush()

    tool_calls = [
        SimpleNamespace(
            id=acc["id"],
            function=SimpleNamespace(name=acc["name"], arguments=acc["arguments"]),
        )
        for _, acc in sorted(tool_call_acc.items())
    ] or None
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="".join(content_parts), tool_calls=tool_calls),
            finish_reason=finish_reason,
        )],
        usage=usage,
    )


# Human-readable progress labels emitted via on_token before each tool executes.
# Gives the UI/Telegram instant feedback during blocking tool rounds.
_TOOL_PROGRESS_LABELS: dict[str, str] = {
    "ask_web_search":     "🔍 Searching the web…",
    "ask_browser_agent":  "🌐 Opening browser…",
    "ask_local_agent":    "📂 Working on files…",
    "ask_read_file":      "📄 Reading file…",
    "ask_write_file":     "✏️ Writing file…",
    "ask_delete_file":    "🗑️ Deleting…",
    "ask_rename_file":    "✂️ Renaming…",
    "ask_run_command":    "⚙️ Running command…",
    "ask_macos_native":   "🍎 macOS action…",
    "ask_email_agent":    "📧 Checking email…",
    "ask_tasks_agent":    "✅ Managing tasks…",
    "ask_calendar_agent": "📅 Checking calendar…",
}

# provider prefix -> (base_url, env var holding the API key)
_PROVIDERS: dict[str, tuple[str, str]] = {
    "openrouter/": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepseek/": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    # OpenAI direct (OPENAI_REAL_API_KEY) — last-resort tier when OpenRouter and
    # DeepSeek both fail (verified live 2026-07-31: key valid, chat completions
    # work). gpt-4o-mini is the cheap tier; only used on failure paths.
    "openai/": ("https://api.openai.com/v1", "OPENAI_REAL_API_KEY"),
 }

# NOTE: this account's OpenRouter key is provider-restricted (Settings -> Preferences
# -> allowed providers, or a zero-retention data policy) to {anthropic, cloudflare,
# google-ai-studio} — DeepSeek and OpenAI models 404 through OpenRouter with "No
# allowed providers are available" regardless of which model is requested. Verified
# live 2026-07-04. DeepSeek's own direct API (DEEPSEEK_API_KEY, no OpenRouter involved)
# bypasses this entirely — see the "deepseek/" prefix above.
#
# Live benchmarking (tests/benchmark_models_tool_calling.py, 2026-07-04), same 5-case
# suite, same LangGraph agent loop:
#   deepseek/deepseek-v4-flash (direct)    4/5 — only miss is a harness/specialist
#                                                quirk (short-circuits before the tool
#                                                loop runs), not a model failure
#   openrouter/anthropic/claude-haiku-4.5  4/5 — same single miss, same reason
#   openrouter/google/gemini-3.1-flash-lite 2/5 — hit the exact repetitive-loop drift
#                                                 documented for local gemma4 (CLAUDE.md
#                                                 "qwen3:8b Agentic Behavior"), plus one
#                                                 empty-response failure
# DeepSeek ties Haiku on reliability and is the cheaper option (the original ask), so
# it's primary. Haiku stays as fallback — different provider/infra from DeepSeek, which
# matters for the escalation path (tool_node's 3-consecutive-error swap) actually being
# an independent second try, not the same failure mode twice. Flash-lite dropped from
# the rotation entirely — demonstrated real agentic weakness, only use it for simple
# single-tool tasks if cost ever requires a third tier.
DEFAULT_CLOUD_MODEL = "deepseek/deepseek-v4-flash"
CLOUD_FALLBACK_MODEL = "openrouter/google/gemini-2.5-flash-lite"
# Last-resort tier: reached only when both above fail (dead provider / 402 /
# 5xx / network). OpenAI direct via OPENAI_REAL_API_KEY, cheap tier.
OPENAI_FALLBACK_MODEL = "openai/gpt-4o-mini"
# Cheapest vision-capable model actually reachable on this provider-restricted
# OpenRouter key (see the {anthropic, cloudflare, google-ai-studio} note above) —
# every other cheap vision option tested (Qwen-VL, Nemotron, Nova, DeepInfra-hosted
# Gemma) 404s with "no allowed providers". $0.10/$0.40 per M vs claude-haiku-4.5's
# $1/$5, verified live 2026-07-05 with a real OCR read against a synthetic test image.
# gemini-2.5-flash-lite retired by Google (404 "no longer available", hit live
# 2026-07-09) — swapped to gemini-3.1-flash-lite, same $0.10/$0.40 pricing tier,
# re-verified live with a real OCR read the same day. Note: 3.1-flash-lite scored
# 2/5 on the agentic tool-calling benchmark above (repetitive-loop drift) — that's
# the multi-round tool-loop case, irrelevant here since vision calls are single-shot
# OCR reads, not agentic chains.
CLOUD_VISION_MODEL = "openrouter/google/gemini-3.1-flash-lite"
MAX_ROUNDS = 25

_clients: dict[str, OpenAI] = {}
_dead_providers: dict[str, float] = {}  # prefix -> timestamp when marked dead
_DEAD_RETRY_AFTER = 86400  # retry a dead provider after 24h

# Cumulative token usage per real model name — read by callers (e.g. the
# reliability benchmark) that want cost/usage visibility across a run.
# Not thread-safe; fine for the sequential benchmark/CLI use cases this serves.
_usage_totals: dict[str, dict[str, int]] = {}


def get_usage_totals() -> dict[str, dict[str, int]]:
    return {k: dict(v) for k, v in _usage_totals.items()}


def reset_usage_totals() -> None:
    _usage_totals.clear()


def _track_usage(real_model: str, usage) -> None:
    if not usage:
        return
    totals = _usage_totals.setdefault(real_model, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
    _p = getattr(usage, "prompt_tokens", 0) or 0
    _c = getattr(usage, "completion_tokens", 0) or 0
    totals["prompt_tokens"] += _p
    totals["completion_tokens"] += _c
    totals["calls"] += 1
    record_usage(_p, _c)


def is_cloud_model(model: str) -> bool:
    return any(model.startswith(p) for p in _PROVIDERS)


def _provider_for(model: str) -> str | None:
    for prefix in _PROVIDERS:
        if model.startswith(prefix):
            return prefix
    return None


def _is_dead(model: str) -> bool:
    prefix = _provider_for(model)
    if prefix and prefix in _dead_providers:
        dead_since = _dead_providers[prefix]
        if time.time() - dead_since < _DEAD_RETRY_AFTER:
            return True
        _dead_providers.pop(prefix, None)
    return False


def _mark_dead(model: str) -> None:
    prefix = _provider_for(model)
    if prefix:
        _dead_providers[prefix] = time.time()
        _log.warning("[cloud_client] marked %s as dead (payment/auth failure), skipping it for %ds",
                     prefix, _DEAD_RETRY_AFTER)


def _is_payment_error(e: Exception) -> bool:
    if getattr(e, "status_code", None) == 402:
        return True
    msg = str(e).lower()
    return "402" in msg or "insufficient" in msg or "requires more credits" in msg


def _resolve(model: str) -> tuple[OpenAI, str]:
    """Model string -> (client for its provider, real model id the provider expects)."""
    for prefix, (base_url, env_key) in _PROVIDERS.items():
        if model.startswith(prefix):
            real_model = model[len(prefix):]
            client = _clients.get(prefix)
            if client is None:
                api_key = os.environ.get(env_key)
                if not api_key:
                    raise RuntimeError(f"{env_key} is not set (tools-harness/.env)")
                client = OpenAI(base_url=base_url, api_key=api_key, timeout=120.0)
                _clients[prefix] = client
            return client, real_model
    raise ValueError(f"not a cloud model (no matching provider prefix): {model}")


def _build_messages(
    user_message: str, history: list, system_prompt: str = None, images: list = None,
) -> list:
    messages: list = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for turn in history or []:
        messages.append({"role": turn["role"], "content": turn["content"]})
    if images:
        # ollama_client's `images` convention is raw base64 strings (no data-URL
        # prefix) — OpenAI-compatible vision content blocks need the prefix.
        content = [{"type": "text", "text": user_message}] + [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}
            for img in images
        ]
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user_message})
    return messages


_SCREENSHOT_PATH_RE = re.compile(r"Screenshot saved:\s*(\S+\.png)")


def _inject_screenshot_image(messages: list, tool_name: str, result: str, model: str) -> None:
    """After take_screenshot succeeds on a vision-capable model, attach the actual
    image as a follow-up user turn instead of leaving the model to call ocr_image.

    ocr_image (PaddleOCR, CPU-only) measured 28.4s on a single full-resolution
    Retina screenshot with dense text (trace_store run 51c47055, 2026-07-10) —
    the entire "what's on my screen" latency was this one call, not model choice
    or screenshot capture (205ms). CLOUD_VISION_MODEL can read the pixels
    directly in the same network round-trip it already pays for, so for vision
    queries this bypasses the CPU bottleneck entirely. ocr_image stays available
    as a tool for when exact extracted text (not a description) is actually
    needed — this only skips it as the default path.
    """
    if model != CLOUD_VISION_MODEL or tool_name != "take_screenshot":
        return
    m = _SCREENSHOT_PATH_RE.search(result)
    if not m:
        return
    try:
        img_bytes = Path(m.group(1)).read_bytes()
    except OSError:
        return
    b64 = base64.b64encode(img_bytes).decode()
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": (
                "Here is the screenshot you just captured. Describe or read it "
                "directly from the image — no need to call ocr_image unless you "
                "specifically need exact extracted text rather than a description."
            )},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    })


# Every known shape of "model leaked its tool-call syntax into plain text
# instead of populating the structured tool_calls field" seen across
# DeepSeek (DSML full-width-pipe tokens), Llama/Mistral/Qwen-style special
# tokens, and generic XML-ish tool-call tags. Add a new alternative here the
# next time an unrecognized leak format shows up in the wild rather than
# special-casing it downstream — this is the one place that decides "is this
# garbage".
_LEAK_TOKEN_RE = re.compile(
    r"<(?=[^<>]*｜)[^<>]{0,200}>"                          # hybrid DSML+XML tag: any <...> containing a
                                                            # fullwidth pipe (e.g. <｜｜DSML｜｜tool_calls>,
                                                            # <｜｜DSML｜｜invoke name="..."> ) — must come first
                                                            # so it consumes the whole tag; the narrower
                                                            # alternatives below only matched the inner
                                                            # ｜｜marker｜｜ and left "<tool_calls>"/"<invoke
                                                            # name=...>" as residual text (confirmed live
                                                            # 2026-07-10, "should I bring an umbrella")
    r"|<｜[^>]{0,60}｜>"                                    # DeepSeek <｜...｜> special tokens
    r"|｜｜[^｜]{0,60}｜｜"                                  # DSML doubled full-width-pipe marker
    r"|<\|[^>]{0,60}\|>"                                   # generic <|special_token|> (Llama/Mistral/Qwen)
    r"|</?\s*(?:tool_calls?|function_calls?|invoke|parameter)\b[^>]*>"  # XML-ish tool-call tags
    r"|\[/?TOOL_CALLS\]",                                  # Mistral bracket-style markers
    re.IGNORECASE,
)


def _strip_leak_artifacts(text: str) -> str:
    """Scrub known tool-call-leak tokens out of otherwise-real content. Applied
    unconditionally to every round's content (not just the final answer) —
    an intermediate round can have valid tool_calls AND a leaked token in the
    same message; that content still gets appended to `messages` untouched
    and was observed corrupting the rolling conversation-history summary
    (`store.conversation` 'Fold ... looked corrupted' retries, 2026-07-09).
    Scrubbing at the source is cheaper and safer than only catching it when
    the leak happens to be the model's entire final reply."""
    return _LEAK_TOKEN_RE.sub("", text).strip()


def _is_garbage_response(text: str) -> bool:
    """Empty, or contains ANY known leak-token marker. Deliberately not
    "empty after stripping" — a real DSML/JSON leak usually leaves non-empty
    junk behind even after its tags are stripped (e.g. raw JSON args text),
    which would misclassify a mostly-garbage reply as fine. Any marker match
    means nudge/retry; _strip_leak_artifacts is a separate, narrower fix for
    the case where content never reaches this check at all (a round with
    valid tool_calls whose content field also has a leaked token — see
    _run_tool_loop)."""
    return not text.strip() or bool(_LEAK_TOKEN_RE.search(text))


def _last_tool_step_summary(messages: list) -> str:
    """Best-effort description of the last tool call before a garbage response,
    so a failed recovery tells the user what actually happened instead of a
    content-free apology. Doesn't retry the chain — a blind retry risks
    re-running side-effecting tools (e.g. send_email) a second time."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") != "tool":
            continue
        name = None
        for am in messages[i - 1::-1]:
            if am.get("role") == "assistant" and am.get("tool_calls"):
                for tc in am["tool_calls"]:
                    if tc.get("id") == m.get("tool_call_id"):
                        name = tc["function"]["name"]
                        break
            if name:
                break
        if not name:
            return ""
        result = (m.get("content") or "")[:200]
        failed = is_error_result(result)
        return f" Last step: `{name}` {'failed' if failed else 'ran'} — {result}"
    return ""


def _recover_from_garbage(client, real_model: str, on_token, messages: list, content: str) -> str:
    """Nudge up to twice for a clean answer if content is garbage/empty; fail
    closed with a clean user-facing message if it still doesn't recover.

    Shared by both the per-round early-return path (round produced no
    tool_calls) and the final forced tools=None completion after max_rounds is
    exhausted. That second path used to return raw model output completely
    unchecked — confirmed live 2026-07-07/08 that this is exactly how DSML
    leaks and truncated "let me check X" non-answers reached a real Telegram
    user, every single time a multi-step tool task ran long enough to hit the
    round cap.
    """
    nudge_attempts = 0
    while _is_garbage_response(content) and nudge_attempts < 2:
        nudge_attempts += 1
        _log.warning(
            "[cloud_client] %s response received, sending nudge (attempt %d/2)",
            "Empty" if not content.strip() else "Malformed tool-call leak in", nudge_attempts,
        )
        if any(m.get("role") == "tool" for m in messages):
            nudge_content = "Please synthesize your final response now based on the tool results above."
        else:
            nudge_content = "Please provide a response to the user's query."
        nudge = {"role": "user", "content": nudge_content}
        resp = _stream_completion(client, on_token, model=real_model, messages=messages + [nudge])
        content = resp.choices[0].message.content or ""
    if _is_garbage_response(content):
        step = _last_tool_step_summary(messages)
        return (
            "I ran into trouble completing that." + step +
            " You can ask me to continue from there, or rephrase the request."
        )
    return _strip_leak_artifacts(content)


def _with_deadline(call: Callable, on_token: Optional[Callable[[str], None]], timeout: float, model: str = None):
    """Run a streaming completion call under a hard wall-clock deadline.

    The OpenAI SDK's own `timeout=` kwarg is a per-chunk httpx read timeout, not a
    total-duration one — a response that trickles data continuously (confirmed live:
    DeepSeek rounds taking 22-123s vs. the normal 2-4s) never trips it. Runs the call
    on a worker thread and bounds it with future.result(timeout=), which IS wall-clock.

    On timeout, the request keeps running server-side (Python threads can't be killed)
    and any further on_token calls from it are dropped via `_live` — the caller is
    expected to raise/fall back to a different model, and a second stream must not be
    interleaved with straggler tokens from the abandoned one. Deliberately does NOT use
    `with ThreadPoolExecutor() as ex:` — __exit__ calls shutdown(wait=True), which blocks
    until the abandoned thread finishes anyway, defeating the timeout entirely.
    """
    if not timeout:
        return call(on_token)

    _live = threading.Event()
    _live.set()

    def _guarded_token(text: str) -> None:
        if _live.is_set() and on_token:
            on_token(text)

    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(call, _guarded_token if on_token else None)
    _t0 = time.time()
    try:
        result = fut.result(timeout=timeout)
        if model:
            from clients import routing_stats
            routing_stats.record(model, ok=True, latency_ms=(time.time() - _t0) * 1000)
        return result
    except _FutureTimeoutError:
        _live.clear()
        if model:
            from clients import routing_stats
            routing_stats.record(model, ok=False, latency_ms=(time.time() - _t0) * 1000)
        raise TimeoutError(f"round exceeded {timeout}s wall-clock deadline")
    except Exception:
        if model:
            from clients import routing_stats
            routing_stats.record(model, ok=False, latency_ms=(time.time() - _t0) * 1000)
        raise
    finally:
        ex.shutdown(wait=False)


def _run_tool_loop(
    model: str, messages: list, tools: list, on_token: Optional[Callable[[str], None]], max_rounds: int,
    fallback_model: str = None, run_id: str = None, force_tool_choice: str = None,
    reasoning_effort: str = None, round_timeout: float = None,
) -> str:
    client, real_model = _resolve(model)
    _reasoning = _reasoning_extra_body(model, reasoning_effort)
    # ponytail: models (DeepSeek observed live) sometimes call a tool name that
    # exists in the global registry but was never offered in this round's schema
    # (e.g. list_calendar_events instead of the exposed ask_calendar_agent) —
    # execute_tool() has no allowlist, so it silently ran anyway, bypassing the
    # orchestrator's subagent fan-out design entirely (all 6 golden queries
    # failed the same night on exactly this pattern). Reject at dispatch instead.
    _allowed_tool_names = {t["function"]["name"] for t in (tools or []) if "function" in t}
    content = ""
    # Mirrors local_agent_nodes.tool_node's mid-run escalation: after 3 consecutive
    # tool-result errors on the primary model, switch to fallback_model for the
    # remaining rounds instead of grinding on with a model that's already failing.
    # Keeps this flat loop and the LangGraph loop equally resilient instead of one
    # being strictly more forgiving than the other.
    consecutive_errors = 0
    escalated = False

    # Latency: force_tool_choice means the harness already decided WHICH tool to
    # call (grounded sports/temporal queries). The model-driven round0 would be a
    # full serial round-trip whose only output is the tool's `arguments` — and the
    # forced tools (ask_web_search/ask_research_agent) re-derive the query inside
    # their own subagent rewriter anyway, so that output is redundant. Execute the
    # forced tool directly and seed the messages so the loop starts at synthesis,
    # saving one round-trip (~1.5s) on every grounded query. Trace/label/message
    # machinery is replicated identically to the model-driven path so
    # verify-on-absence / golden-suite / subagent-envelope see the same entries.
    # Idempotency: chat()'s fallback retry reuses the (already-mutated) messages
    # list, so skip if a tool result is already seeded.
    if force_tool_choice and not any(m.get("role") == "tool" for m in messages):
        _q = ""
        _last = messages[-1] if messages else {}
        if _last.get("role") == "user" and isinstance(_last.get("content"), str):
            _q = _last["content"]
        _tc_id = "call_forced_0"
        if on_token:
            label = _TOOL_PROGRESS_LABELS.get(force_tool_choice, f"⚙️ Running {force_tool_choice}…")
            on_token(f"\n\n{label}")
        check_aborted()
        _t0 = time.time()
        try:
            # Run in a copied context (like the loop's ThreadPoolExecutor path) so a
            # subagent's CURRENT_RUN_ID.set() stays isolated and doesn't leak into this
            # thread's context — otherwise the orchestrator's own synthesis round logs
            # under the subagent's run_id. Trace attribution uses the run_id param, not
            # the contextvar, so only log prefixes were affected — still worth isolating.
            result = contextvars.copy_context().run(execute_tool, force_tool_choice, {"query": _q})
        except Exception as exc:
            result = f"[error] tool execution failed: {exc}"
        is_error = is_error_result(result)
        consecutive_errors = 1 if is_error else 0
        if run_id:
            _record_trace(run_id, {
                "run_id": run_id, "tool": force_tool_choice,
                "args": {"query": _q[:80]},
                "result_summary": result[:120],
                "elapsed_ms": round((time.time() - _t0) * 1000),
                "error": is_error,
            })
        if is_checkpoint_result(force_tool_choice, result):
            return result
        _preseed_msg = {
            "role": "assistant", "content": "",
            "tool_calls": [{
                "id": _tc_id, "type": "function",
                "function": {"name": force_tool_choice, "arguments": json.dumps({"query": _q})},
            }],
        }
        # DeepSeek thinking mode 400s on a fabricated tool-call turn with no
        # reasoning_content; a short placeholder satisfies it. Other providers reject
        # the unknown field, so gate it to deepseek thinking models only.
        if _is_deepseek_thinking(real_model):
            _preseed_msg["reasoning_content"] = (
                f"The user's request needs a live {force_tool_choice} lookup; calling it now."
            )
        messages.append(_preseed_msg)
        if len(result) > 4000:
            result = result[:3800] + f"\n... [truncated — {len(result)} chars total]"
        result = wrap_external_output(force_tool_choice, result)
        messages.append({"role": "tool", "tool_call_id": _tc_id, "content": result})
        force_tool_choice = None  # loop is now pure synthesis (model already has the result)

    _plan_checkpoint_done = False
    for _round in range(max_rounds):
        check_budget()
        check_aborted()
        # Mid-run plan checkpoint: once, past the halfway point of this loop's
        # max_rounds, if the orchestrator laid out a plan (update_task_plan /
        # store/plan_store.py) with steps still unticked, nudge it to check
        # progress against that plan before continuing — same intent as
        # verify-on-absence/claim-check (harness.py) but mid-loop instead of
        # post-answer, so drift gets caught before rounds run out instead of after.
        if run_id and not _plan_checkpoint_done and _round >= max_rounds // 2:
            _plan_checkpoint_done = True
            from store import plan_store as _plan_store
            _plan = _plan_store.get_plan(run_id)
            if _plan and len(_plan["done"]) < len(_plan["steps"]):
                _pending = [s for i, s in enumerate(_plan["steps"]) if i not in set(_plan["done"])]
                messages.append({
                    "role": "system",
                    "content": (
                        "Checkpoint: you're halfway through your round budget. Pending plan "
                        f"steps not yet marked done: {_pending}. Continue working through them, "
                        "or call update_task_plan to correct the plan if it's no longer accurate."
                    ),
                })
        start = time.time()
        # ponytail: forcing tool_choice only on round 0 — the model must ground its
        # first move (e.g. web search) on live/temporal queries instead of being free
        # to answer straight from parametric knowledge, but stays free-choice after
        # that so it can still synthesize/reply normally once grounded.
        _extra = {}
        if force_tool_choice and _round == 0:
            _extra["tool_choice"] = {"type": "function", "function": {"name": force_tool_choice}}
        _call = lambda _on_token: _stream_completion(
            client, _on_token,
            model=real_model,
            messages=messages,
            tools=tools or None,
            max_tokens=8192,
            **_extra, **_reasoning,
        )
        resp = _with_deadline(_call, on_token, round_timeout, model=real_model)
        usage = getattr(resp, "usage", None)
        _track_usage(real_model, usage)
        # 2026-07-11: diagnostic-only addition — finish_reason was never logged
        # or checked anywhere in this file, so a response silently cut short by
        # the API's own (unset here) max_tokens ceiling looked identical to a
        # genuinely complete answer. No max_tokens is passed to _stream_completion
        # anywhere in this module. Logging finish_reason to confirm/rule this out
        # as the cause of truncated voice replies before changing any behavior.
        _finish_reason = getattr(resp.choices[0], "finish_reason", None) if resp.choices else None
        _log.info(
            "[cloud_client] model=%s round=%d elapsed=%.2fs prompt_tokens=%s completion_tokens=%s finish_reason=%s",
            real_model, _round, time.time() - start,
            getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None), _finish_reason,
        )

        msg = resp.choices[0].message
        content = msg.content or ""
        tool_calls = msg.tool_calls

        if not tool_calls:
            return _recover_from_garbage(client, real_model, on_token, messages, content)

        # A round can have valid tool_calls AND a leaked token in the same
        # message's content — strip before it enters history, or it silently
        # corrupts the rolling conversation summary later (see
        # store.conversation's "Fold ... looked corrupted" retry guard).
        content = _strip_leak_artifacts(content)

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        check_aborted()
        # Emit a progress label for each tool call before blocking execution begins,
        # so the UI / Telegram shows immediate feedback instead of a frozen bubble.
        if on_token:
            for tc in tool_calls:
                label = _TOOL_PROGRESS_LABELS.get(
                    tc.function.name, f"⚙️ Running {tc.function.name}…"
                )
                on_token(f"\n\n{label}")
        # copy_context() snapshots CURRENT_RUN_ID (and any other contextvars) from this
        # thread; ThreadPoolExecutor workers otherwise start with a fresh default context,
        # which was silently dropping the run_id tag from every tool call's logs (including
        # third-party loggers like googleapiclient) — see log_config.py's RunIdFilter.
        # Each submitted call needs its OWN copy — a Context object isn't reentrant, so
        # sharing one across concurrently-running workers throws "already entered".
        with ThreadPoolExecutor(max_workers=min(len(tool_calls), 4)) as executor:
            futures = {}
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                print(f"[tool] {tc.function.name}({str(args)[:200]})", flush=True)
                _ctx = contextvars.copy_context()
                if tc.function.name not in _allowed_tool_names:
                    _err = f"[error] tool '{tc.function.name}' was not offered this round — use one of the available tools instead"
                    futures[executor.submit(lambda e=_err: e)] = tc
                else:
                    futures[executor.submit(_ctx.run, execute_tool, tc.function.name, args)] = tc

            for future, tc in futures.items():
                _t0 = time.time()
                try:
                    result = future.result()
                except Exception as exc:
                    result = f"[error] tool execution failed: {exc}"
                is_error = is_error_result(result)
                consecutive_errors = consecutive_errors + 1 if is_error else 0
                if run_id:
                    try:
                        args_preview = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args_preview = {}
                    _record_trace(run_id, {
                        "run_id": run_id, "tool": tc.function.name,
                        "args": {k: str(v)[:80] for k, v in args_preview.items()},
                        "result_summary": result[:120],
                        "elapsed_ms": round((time.time() - _t0) * 1000),
                        "error": is_error,
                    })
                if is_checkpoint_result(tc.function.name, result):
                    return result
                if len(result) > 4000:
                    result = result[:3800] + f"\n... [truncated — {len(result)} chars total]"
                result = wrap_external_output(tc.function.name, result)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                _inject_screenshot_image(messages, tc.function.name, result, model)

        if consecutive_errors >= 3 and not escalated and fallback_model and fallback_model != model:
            _log.warning(
                "[cloud_client] %d consecutive tool errors on %s, escalating to %s",
                consecutive_errors, model, fallback_model,
            )
            client, real_model = _resolve(fallback_model)
            _reasoning = _reasoning_extra_body(fallback_model, reasoning_effort)
            consecutive_errors = 0
            escalated = True
            if run_id:
                # Machine-detectable escalation marker — the golden-query regression
                # suite asserts forbid_escalation against this trace entry.
                _record_trace(run_id, {
                    "run_id": run_id, "tool": "_model_escalation",
                    "args": {"from": model, "to": fallback_model},
                    "result_summary": "escalated after consecutive tool errors",
                    "elapsed_ms": 0, "error": False, "escalated": True,
                })
    if tool_calls:
        check_aborted()
        _call = lambda _on_token: _stream_completion(
            client, _on_token,
            model=real_model,
            messages=messages,
            tools=None,
            **_reasoning,
        )
        resp = _with_deadline(_call, on_token, round_timeout, model=real_model)
        content = resp.choices[0].message.content or ""
        return _recover_from_garbage(client, real_model, on_token, messages, content)

    return _strip_leak_artifacts(content)


def raw_completion(model: str, messages: list, tools: list, temperature: float = 0.7, bypass_budget: bool = False):
    """
    Single (non-looping) chat completion for callers that run their own
    tool-execution loop externally (the LangGraph local-agent — see
    local_agent_nodes.py's call_model). Returns the OpenAI SDK's Choice
    object; its .message.content/.tool_calls shape is close enough to
    ollama's ChatResponse.message that both share the same parsing code.

    Normalizes any replayed assistant tool_calls' `function.arguments` from
    dict back to a JSON string — required by the OpenAI-compatible schema,
    but the caller round-trips them as dicts (ollama's own convention).

    Raises BudgetExceededError if today's daily token budget is already used up
    and bypass_budget is False. Cloud is the permanent default — callers should
    retry with bypass_budget=True rather than downgrading to local gemma4.
    """
    if not bypass_budget:
        check_budget()
    if _is_dead(model) and model != OPENAI_FALLBACK_MODEL:
        _log.info("[cloud_client] raw_completion %s's provider is dead, using OpenAI fallback %s", model, OPENAI_FALLBACK_MODEL)
        model = OPENAI_FALLBACK_MODEL
    client, real_model = _resolve(model)

    sent = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            m = dict(m)
            m["tool_calls"] = [
                {
                    **tc,
                    "function": {
                        **tc["function"],
                        "arguments": (
                            json.dumps(tc["function"]["arguments"])
                            if isinstance(tc["function"]["arguments"], dict)
                            else tc["function"]["arguments"]
                        ),
                    },
                }
                for tc in m["tool_calls"]
            ]
        sent.append(m)

    try:
        resp = _create_with_retry(
            client,
            model=real_model,
            messages=sent,
            tools=tools or None,
            temperature=temperature,
        )
    except Exception as e:
        if model == OPENAI_FALLBACK_MODEL:
            raise
        if _is_payment_error(e):
            _mark_dead(model)
        _log.warning("[cloud_client] raw_completion %s failed (%s), retrying on OpenAI fallback %s",
                     model, e, OPENAI_FALLBACK_MODEL)
        client, real_model = _resolve(OPENAI_FALLBACK_MODEL)
        resp = _create_with_retry(
            client,
            model=real_model,
            messages=sent,
            tools=tools or None,
            temperature=temperature,
        )
    _track_usage(real_model, getattr(resp, "usage", None))
    return resp.choices[0]

def chat(
    user_message: str,
    tools: list = None,
    model: str = DEFAULT_CLOUD_MODEL,
    history: list = None,
    on_token: Optional[Callable[[str], None]] = None,
    system_prompt: str = None,
    max_rounds: int = MAX_ROUNDS,
    images: list = None,
    options: dict = None,
    fallback_model: str = CLOUD_FALLBACK_MODEL,
    run_id: str = None,
    bypass_budget: bool = False,
    force_tool_choice: str = None,  # name of a tool to force on round 0 only (e.g.
    # "ask_web_search") — for queries that must not be answered from bare model
    # knowledge (live scores/news/prices). See _run_tool_loop.
    reasoning_effort: str = None,  # "low"/"medium"/"high" adaptive-thinking budget,
    # forwarded as OpenRouter's reasoning.effort — see clients.router.reasoning_effort_for_intent
    # and _reasoning_extra_body. Silently ignored on non-openrouter/ models (DeepSeek direct API).
    round_timeout: float = None,  # per-round API call timeout (seconds), overriding the
    # client's 120s default for just this call. On timeout, chat()'s existing
    # cross-provider fallback below kicks in and retries on fallback_model with the
    # same messages (partial tool results included) — bounds worst-case latency on a
    # slow individual round without cutting max_rounds or losing gathered context.
) -> str:
    """
    Send a message through the cloud tool loop via OpenRouter.

    On an API exception, retries the whole call once against fallback_model
    before raising. Mid-loop, after 3 consecutive tool-result errors on the
    primary model, `_run_tool_loop` also escalates to fallback_model on its own
    (same behavior local_agent_nodes.tool_node's LangGraph loop already has —
    see its module docstring). `options` is accepted for signature parity with
    ollama_client.chat() but unused. `images` (raw base64 strings, ollama's
    convention) IS supported when `model` is a vision-capable OpenRouter model
    (verified live 2026-07-05: google/gemini-2.5-flash-lite reads text out of
    an image correctly) — most cloud tool-calling models here are text-only,
    so only pass images when routing to a known vision model.

    `run_id`, when given, logs each tool call into store.trace_store under
    that id — same shared trace the LangGraph local-agent already writes to,
    so a request served by this flat loop gets an inspectable trace too.

    Raises BudgetExceededError if today's daily token budget is already used up
    and bypass_budget is False. Cloud is the permanent default (2026-07 revamp) —
    callers should retry with bypass_budget=True rather than downgrading to local
    gemma4, which is unreliable past a few chained tool calls.
    """
    if not bypass_budget:
        check_budget()
    # If primary model's provider is dead (payment/auth failure), skip straight
    # to fallback instead of wasting time on a call that will 402.
    if _is_dead(model):
        # CLOUD_FALLBACK_MODEL is cross-provider (DeepSeek direct, own API key/
        # credits) — skip straight to it instead of wasting a call on the same
        # exhausted OpenRouter account. OpenAI stays the last-resort tier below
        # if DeepSeek also fails.
        _log.info("[cloud_client] %s's provider is dead, using fallback %s", model, CLOUD_FALLBACK_MODEL)
        model = CLOUD_FALLBACK_MODEL
        fallback_model = OPENAI_FALLBACK_MODEL
    if images and model != CLOUD_VISION_MODEL:
        # Only CLOUD_VISION_MODEL is verified to accept image_url content blocks —
        # DeepSeek 400s on them outright ("unknown variant image_url, expected
        # text"), verified live 2026-07-05. Drop rather than crash; caller still
        # gets a text answer instead of a hard failure.
        _log.warning("[cloud_client] images passed to non-vision model %s — dropping", model)
        images = None
    tools = tools or []
    messages = _build_messages(user_message, history or [], system_prompt=system_prompt, images=images)

    try:
        return _run_tool_loop(
            model, messages, tools, on_token, max_rounds,
            fallback_model=fallback_model, run_id=run_id, force_tool_choice=force_tool_choice,
            reasoning_effort=reasoning_effort, round_timeout=round_timeout,
        )
    except Exception as e:
        if model == fallback_model:
            raise
        if isinstance(e, QueryAbortedException):
            # 2026-07-11: a caller-side soft-timeout (e.g. brabble_hook.py's 45s
            # "voice users can't wait forever" timer) hits /chat/abort, which sets
            # the SAME threading.Event this thread's abort_event points to for the
            # whole chat_id. That event doesn't auto-clear, so this fallback retry
            # used to hit check_aborted() on its very first round and raise the
            # identical QueryAbortedException immediately — the OpenRouter fallback
            # below was effectively dead code for aborts, silently cascading every
            # abort straight to local_chat()'s LAST-resort local gemma4:12b-mlx
            # (unreliable past a few chained tool calls, confirmed live: truncated/
            # garbled voice replies). Clear the SAME event object (not a fresh
            # unregistered one) so a genuinely NEW abort — real barge-in — still
            # reaches this thread via chat_ui.py's _active_streams, while this
            # stale one no longer kills the retry we're about to make.
            _ae = getattr(threading.current_thread(), "abort_event", None)
            if _ae is not None:
                _ae.clear()
        if isinstance(e, TimeoutError):
            # 2026-07-11: _with_deadline() can't actually kill the abandoned worker
            # thread (Python threads aren't killable) — its streaming HTTP connection
            # stays open server-side and keeps its slot in _clients' cached OpenAI
            # client's connection pool, which is a process-wide singleton reused for
            # every request (see _resolve()). Confirmed live: a later round on the
            # same model got zero httpx log line at all (unlike every other round,
            # which logs "POST ... 200 OK") — consistent with it queueing for a pool
            # slot rather than getting a slow response. Evict the cached client so
            # the next call builds a fresh pool instead of accumulating stuck
            # connections across a long-running process; doesn't affect the
            # abandoned thread itself, only prevents future calls from sharing its pool.
            for _prefix in list(_PROVIDERS):
                if model.startswith(_prefix):
                    _old = _clients.pop(_prefix, None)
                    if _old is not None:
                        try:
                            _old.close()
                        except Exception:
                            pass
                    break
        if _is_payment_error(e):
            _mark_dead(model)
        _log.warning("[cloud_client] %s failed (%s), retrying on fallback %s", model, e, fallback_model)
        try:
            return _run_tool_loop(
                fallback_model, list(messages), tools, on_token, max_rounds,
                run_id=run_id, force_tool_choice=force_tool_choice, reasoning_effort=reasoning_effort,
                round_timeout=round_timeout,
            )
        except Exception as e2:
            if fallback_model == OPENAI_FALLBACK_MODEL:
                raise
            _log.warning("[cloud_client] fallback %s also failed (%s), last resort OpenAI %s",
                         fallback_model, e2, OPENAI_FALLBACK_MODEL)
            return _run_tool_loop(
                OPENAI_FALLBACK_MODEL, list(messages), tools, on_token, max_rounds,
                run_id=run_id, force_tool_choice=force_tool_choice, reasoning_effort=reasoning_effort,
                round_timeout=round_timeout,
            )
