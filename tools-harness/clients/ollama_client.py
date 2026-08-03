"""
Ollama client with tool loop and conversation history support.
"""

import json
import logging
import os
import re
import time
import concurrent.futures
import contextvars
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from typing import Callable, Optional
import ollama
from ollama import Message as OllamaMessage
from tools.registry import execute_tool, is_error_result, is_checkpoint_result
from tools.premise_check import inject as _premise_inject
from tools.injection_guard import wrap_external_output

_SEARCH_TOOL_NAMES = {"web_search", "google_search", "brave_search", "tech_search"}


_TEXT_TOOL_CALL_RE = re.compile(
    r'\{[^{}]*"name"\s*:\s*"([^"]+)"[^{}]*"arguments"\s*:\s*(\{[^{}]*\})[^{}]*\}',
    re.DOTALL,
)


from clients.cancellation import QueryAbortedException, check_aborted
from store.trace_store import record as _record_trace

import threading

# Tools that re-enter Ollama internally (own model call). On a single-GPU 24GB box,
# letting several of these run concurrently stacks multiple gemma4:12b-mlx generations and
# evicts/OOMs the warm set. Gate them through one semaphore so at most one model-invoking
# tool runs at a time; plain IO tools (fs, http) still overlap freely.
# ponytail: global single-permit gate. Raise the count only if you move off shared-memory hardware.
_MODEL_INVOKING_TOOLS = {"web_search", "deep_research", "local_search"}
_model_tool_sema = threading.Semaphore(1)


def _run_tool_gated(name: str, arguments):
    """execute_tool, but serialize model-invoking tools through _model_tool_sema."""
    if name in _MODEL_INVOKING_TOOLS:
        with _model_tool_sema:
            return execute_tool(name, arguments)
    return execute_tool(name, arguments)


def _parse_text_tool_call(content: str, available_tool_names: set) -> tuple[str, dict] | None:
    """
    Detect tool call JSON emitted as text content (gemma4 at temp=0 quirk).
    Returns (fn_name, arguments) if a known tool call is found, else None.
    """
    if not content or not available_tool_names:
        return None
    m = _TEXT_TOOL_CALL_RE.search(content)
    if m:
        fn_name = m.group(1)
        if fn_name in available_tool_names:
            try:
                args = json.loads(m.group(2))
                return fn_name, args
            except json.JSONDecodeError:
                pass
    return None


# Single source of truth for the default local model. Override per machine via
# OLLAMA_DEFAULT_MODEL in .env (scripts/profile_hardware.py writes it). Runtime
# sites should import this instead of hardcoding a model name.
DEFAULT_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "gemma4:12b-mlx")
MAX_ROUNDS = 25

# Human-readable progress labels emitted via on_token before each tool executes.
# Imported by cloud_client too — keep in sync.
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


_CLIENT = None

# Ollama client requests previously had NO timeout (ollama lib defaults to
# httpx Timeout(None) = infinite). A hung daemon / stuck model load / dead
# host would block the calling thread forever. Set a bounded per-request
# timeout; override via OLLAMA_TIMEOUT (seconds).
_OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "300"))

# Hard cap on a single tool-execution round inside the local tool loop. A tool
# with no internal timeout (ffmpeg, yt-dlp, a stuck socket) must not freeze the
# round forever. Default 120s — generous for real work, finite enough to unblock.
_TOOL_ROUND_TIMEOUT_S = float(os.environ.get("OLLAMA_TOOL_TIMEOUT", "120"))


def _get_client() -> ollama.Client:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    host = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL")
    if host:
        _CLIENT = ollama.Client(host=host, timeout=_OLLAMA_TIMEOUT)
    else:
        _CLIENT = ollama.Client(timeout=_OLLAMA_TIMEOUT)
    return _CLIENT


# Per-model addenda appended to the base system prompt.
# Add an entry here when a model needs explicit behavioral instruction
# that other models handle implicitly.
# COUPLED CONTRACT — DO NOT CHANGE IN ISOLATION:
# The [SEARCH NOTE: ...] sentinel format is injected by tools/premise_check.py.
# The instruction below tells gemma4 to act on it. If the sentinel format changes
# in premise_check.py, update this instruction to match, then re-run
# test_verify_fixes.py. Changing one without the other causes silent regression.
# Explicit num_ctx per model — Ollama defaults to 2048 which truncates responses.
# Values chosen to be practical on 24 GB unified memory without exhausting VRAM.
# 12b officially supports 256K; we use 16384 to keep memory headroom (KV cache
# is q8_0 quantized via OLLAMA_KV_CACHE_TYPE env var). Bump to 32768 for
# vision / long-doc tasks if memory allows.
_MODEL_NUM_CTX: dict[str, int] = {
    "gemma4": 16384,
    "gemma4:e2b": 16384,
    "gemma4:12b-mlx": 16384,
    "qwen3:8b": 16384,
    "qwen3:4b": 16384,
    "qwen3.5:4b": 16384,
}

_MODEL_ADDENDA: dict[str, str] = {
    "gemma4": (
        "IMPORTANT: If search results contradict the user's premise or question, "
        "state the contradiction explicitly before answering — do not confirm false claims. "
        "If you see [SEARCH NOTE: ...] in a tool result, follow that instruction exactly. "
        "If search returns [search_failed ...], tell the user you could not find reliable "
        "information and do not guess."
    ),
    "qwen3:4b": (
        "Your training data cutoff is approximately late 2024. "
        "For any question about events, decisions, launches, scores, prices, or news from 2025 onward: "
        "use web_search first and do not answer from memory."
    ),
    "qwen3:8b": (
        "Your training data cutoff is approximately late 2024. "
        "For any question about events, decisions, launches, scores, prices, or news from 2025 onward: "
        "use web_search first and do not answer from memory."
    ),
}


@lru_cache(maxsize=16)
def _base_prompt_static(model: str) -> str:
    """Static portion of the system prompt — cached per model, rebuilt only on restart."""
    static = (
        "You are a helpful, thoughtful assistant. Answer questions thoroughly and directly.\n\n"
        "RULES:\n"
        "- Be substantive: give complete answers with reasoning, context, and specifics.\n"
        "- Use structure: break long answers into sections with headings, lists, or paragraphs.\n"
        "- Include numbers and facts: dates, counts, names, and specifics whenever relevant.\n"
        "- Be direct: state the answer first, then elaborate. No throat-clearing.\n\n"
        "KNOWLEDGE:\n"
        "Your training data has a knowledge cutoff of approximately late 2024. "
        "For questions about events, news, rulings, launches, scores, prices, or announcements"
        "dated 2025 or later, use web_search — your weights do not contain this information. "
        "For general knowledge, definitions, or established facts you are confident about, "
        "answer directly from your training data.\n\n"
        "TOOLS:\n"
        "When using tools: always fetch data before reasoning about it. "
        "If required parameters (title, date/time) are missing from the user's request, ask — never invent values. "
        "If search returns insufficient results, tell the user honestly — do not fabricate. "
        "If a tool result indicates an error or failure, tell the user the action did not succeed — "
        "never report success when the tool result says otherwise."
    )
    addendum = _MODEL_ADDENDA.get(model, "")
    return f"{static}\n\n{addendum}" if addendum else static


def _system_prompt(model: str = "") -> str:
    """Build system prompt: return the cached static base."""
    return _base_prompt_static(model)


def _build_messages(
    user_message: str,
    history: list,
    system_prompt: str = None,
    images: list = None,
    model: str = "",
    tools: list = None,
) -> list:
    """
    Assemble the message list:
      [system] + [history turns] + [current user message]

    History turns are injected as real chat messages so the model can resolve
    references like "this deadline" or "explain why" from prior context.

    Gemma4 function calling requires a 'developer' role system message (not 'system')
    with the activation phrase prepended. See: https://ai.google.dev/gemma/docs/gemma-4
    """
    base = system_prompt or _system_prompt(model)

    _is_gemma = model.startswith("gemma")
    if _is_gemma and tools:
        # Gemma4 function calling activation phrase required by model card
        sys_content = (
            "You are a model that can do function calling with the following functions.\n\n" + base
        )
        sys_role = "developer"
    else:
        sys_content = base
        sys_role = "system"

    # Prepend local date/time to the user message to keep system prompt static for prefix caching
    now = datetime.now().astimezone()
    dt_str = now.strftime("%A, %B %d %Y, %I:%M %p %Z")
    contextual_user_message = f"[Current Date/Time: {dt_str}]\n\n{user_message}"

    user_msg: dict = {"role": "user", "content": contextual_user_message}
    if images:
        user_msg["images"] = images

    messages: list = [{"role": sys_role, "content": sys_content}]
    for turn in history or []:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append(user_msg)
    return messages


def _run_local(
    model: str, messages: list, tools: list, temperature: float = None, options: dict = None
) -> tuple:
    start = time.time()
    opts = (options or {}).copy()
    if temperature is not None:
        opts["temperature"] = temperature

    # Qwen3 + Gemma4: disable thinking mode for the chat tool loop.
    # Thinking mode (on by default) burns the entire num_predict budget on
    # internal reasoning before producing any output — with num_predict=200 we
    # would get empty answers. Disable for ALL gemma4 calls, not just tool
    # calls: casual chat (no tools) also caps num_predict=200 and would fail.
    # Verified live: gemma4:12b-mlx with think=True, num_predict=300 → 0 words
    # (345 thinking tokens consumed). With think=False → real answers.
    _is_qwen3 = model.startswith("qwen3")
    _is_gemma = model.startswith("gemma")
    _think = False if (_is_qwen3 or _is_gemma) else None
    if _is_qwen3 and tools:
        opts.setdefault("temperature", 0.7)
        opts.setdefault("top_p", 0.8)
        opts.setdefault("top_k", 20)
        opts["repetition_penalty"] = 1.05

    # Gemma4 tool-calling: use Google's recommended sampling params from model card.
    # Docs: temperature=1.0, top_p=0.95, top_k=64 for all use cases including tool calling.
    if _is_gemma and tools:
        opts.setdefault("temperature", 1.0)
        opts.setdefault("top_p", 0.95)
        opts.setdefault("top_k", 64)

    # Enable flash attention for local runs to optimize VRAM and memory bandwidth
    opts.setdefault("flash_attn", True)

    # Always set num_ctx explicitly — Ollama default is 2048 which truncates responses.
    if "num_ctx" not in opts:
        opts["num_ctx"] = _MODEL_NUM_CTX.get(model) or next(
            (v for k, v in _MODEL_NUM_CTX.items() if model.startswith(k)), 8192
        )

    # Cap generation length to avoid gemma4 runaway output on simple prompts.
    # 512 gives enough budget for thorough answers without excessive verbosity.
    if "num_predict" not in opts:
        opts["num_predict"] = 2048 if tools else 512

    resp = _get_client().chat(
        model=model,
        messages=messages,
        tools=tools or None,
        keep_alive=-1,
        options=opts or None,
        think=_think,
    )
    elapsed = time.time() - start
    return resp, elapsed


def _run_streaming(
    model: str, messages: list, tools: list, on_token: Callable[[str], None], options: dict = None
) -> tuple:
    """
    Run with streaming. Calls on_token(chunk) for each content token.
    Tool call rounds produce no content so on_token is silently skipped.
    Returns (content_str, tool_calls_or_None, elapsed, OllamaMessage_for_history).
    """
    start = time.time()
    content_parts: list[str] = []
    tool_calls = None

    opts = (options or {}).copy()

    # Disable thinking for qwen3 OR gemma4 in the streaming path too.
    # See _run_local comment for why — without this, num_predict=200 → empty.
    _is_qwen3_s = model.startswith("qwen3")
    _is_gemma_s = model.startswith("gemma")
    _stream_think = False if (_is_qwen3_s or _is_gemma_s) else None
    if _is_qwen3_s and tools:
        opts.update({"temperature": 0.7, "top_p": 0.8, "top_k": 20, "repetition_penalty": 1.05})
    elif _is_gemma_s and tools:
        opts.update({"temperature": 1.0, "top_p": 0.95, "top_k": 64})

    # Enable flash attention for streaming runs to optimize VRAM and memory bandwidth
    opts.setdefault("flash_attn", True)

    # Always set num_ctx explicitly — Ollama default is 2048 which truncates responses.
    if "num_ctx" not in opts:
        opts["num_ctx"] = _MODEL_NUM_CTX.get(model) or next(
            (v for k, v in _MODEL_NUM_CTX.items() if model.startswith(k)), 8192
        )

    # Cap generation length for streaming calls — same cap as blocking path.
    if "num_predict" not in opts:
        opts["num_predict"] = 2048 if tools else 512

    for chunk in _get_client().chat(
        model=model,
        messages=messages,
        tools=tools or None,
        keep_alive=-1,
        stream=True,
        think=_stream_think,
        options=opts,
    ):
        check_aborted()
        if chunk.message.content:
            content_parts.append(chunk.message.content)
            on_token(chunk.message.content)
        if chunk.message.tool_calls:
            tool_calls = chunk.message.tool_calls

    elapsed = time.time() - start
    content = "".join(content_parts)
    msg = OllamaMessage(role="assistant", content=content, tool_calls=tool_calls)
    return content, tool_calls, elapsed, msg


def warmup(models: list[str] = None):
    """Load models into Ollama RAM and keep them alive indefinitely.

    Pre-allocates KV cache at 16384 + warms the system prompt prefix so
    the first real request doesn't pay KV cache population (5-13s).

    Skips models already resident — Ollama is a separate, persistent daemon
    that survives chat_ui/core.py restarts, so re-issuing a full reload
    (5-18s per model) on every process bounce is pure waste and, worse,
    with OLLAMA_MAX_LOADED_MODELS < len(models) it forces eviction/reload
    thrashing that can stall an unrelated in-flight request waiting on
    whichever model just got evicted (confirmed live 2026-07-10: a trivial
    voice query stalled ~17s behind a 3-model warmup on a 2-slot box).
    """
    import logging
    import datetime as _dt

    log = logging.getLogger(__name__)
    if models is None:
        models = [DEFAULT_MODEL]
        if DEFAULT_MODEL != "gemma4:12b-mlx":
            models.append("gemma4:12b-mlx")
    try:
        _hot = {m["model"] for m in _get_client().ps().get("models", [])}
    except Exception:
        _hot = set()
    models = [m for m in models if m not in _hot]
    if not models:
        log.info("all requested models already warm, skipping")
        return
    log.info("warming Ollama models: %s", ", ".join(models))
    _sp = _system_prompt()  # matches casual chat (no activation phrase)
    from tools._time_context import now_line
    _dt_line = now_line()
    for m in models:
        try:
            _get_client().chat(
                model=m,
                messages=[{"role": "system", "content": _sp}, {"role": "user", "content": _dt_line + "\n\nhi"}],
                keep_alive=-1,
                options={"num_ctx": 16384},
            )
            log.info("warmup ok for %s", m)
        except Exception as e:
            log.warning("warmup failed for %s: %s", m, e)


def chat(
    user_message: str,
    tools: list = None,
    model: str = DEFAULT_MODEL,
    history: list = None,
    on_token: Optional[Callable[[str], None]] = None,
    system_prompt: str = None,
    max_rounds: int = MAX_ROUNDS,
    images: list = None,
    options: dict = None,
    run_id: str = None,
    force_tool_choice: str = None,  # ponytail: cloud-only feature (see cloud_client.chat);
    # accepted here only so local_chat's cloud→local fallback doesn't TypeError on it.
    reasoning_effort: str = None,  # ponytail: cloud-only (OpenRouter reasoning.effort),
    # accepted+ignored here for the same fallback-signature reason as force_tool_choice.
    round_timeout: float = None,  # ponytail: cloud-only (per-round API timeout),
    # accepted+ignored here for the same fallback-signature reason as force_tool_choice.
) -> str:
    """
    Send a message through the local tool loop, optionally with conversation history.
    If on_token is provided, text tokens are streamed to it as they're generated.
    Tool-call rounds are always blocking (Ollama can't stream partial tool_calls JSON).

    `run_id`, when given, logs each tool call into store.trace_store under that
    id — same shared trace the cloud client and LangGraph local-agent write to.
    """
    tools = tools or []

    messages = _build_messages(
        user_message,
        history or [],
        system_prompt=system_prompt,
        images=images,
        model=model,
        tools=tools,
    )

    # gemma4: use temperature=0 for synthesis after tool rounds to maximise determinism.
    # qwen3: handled inside _run_local (think=False + 0.7/0.8/1.05).
    _tool_temp = 0.0 if (tools and model.startswith("gemma")) else None
    _is_qwen3 = model.startswith("qwen3")
    _is_gemma = model.startswith("gemma")
    _think = False if (_is_qwen3 or _is_gemma) else None

    # Parity with cloud_client._run_tool_loop / local_agent_nodes.tool_node: bail out
    # after 3 consecutive tool errors instead of grinding through the remaining rounds.
    # This is the plain local fallback tier (no further model to escalate to — see
    # harness.py's cloud->ollama fallback), so the action is "stop and summarize what
    # we have" rather than switching models. Prevents the documented qwen3/gemma4
    # failure mode (CSS-selector probing loops, hallucination) from burning the
    # full round budget.
    consecutive_errors = 0

    for _round in range(max_rounds):
        check_aborted()
        # After a tool call, tool result messages are in history. gemma4 (and some
        # other models) silently return no content tokens when streaming with tool results
        # in context. Use blocking for those synthesis rounds and emit via on_token once.
        _has_tool_results = any(
            (isinstance(m, dict) and m.get("role") == "tool")
            or (hasattr(m, "role") and m.role == "tool")
            for m in messages
        )

        # Merge defaults with provided options
        active_opts = (options or {}).copy()

        from clients import routing_stats
        _rt0 = time.time()
        try:
            if on_token and not _has_tool_results:
                content, tool_calls, _, msg = _run_streaming(
                    model, messages, tools, on_token, options=active_opts
                )
            elif on_token and _has_tool_results:
                resp, _ = _run_local(
                    model, messages, tools, temperature=_tool_temp, options=active_opts
                )
                content = resp.message.content or ""
                tool_calls = resp.message.tool_calls
                msg = resp.message
            else:
                resp, _ = _run_local(
                    model, messages, tools, temperature=_tool_temp, options=active_opts
                )
                content = resp.message.content or ""
                tool_calls = resp.message.tool_calls
                msg = resp.message
        except Exception:
            routing_stats.record(model, ok=False, latency_ms=(time.time() - _rt0) * 1000)
            raise
        routing_stats.record(model, ok=True, latency_ms=(time.time() - _rt0) * 1000)

        # Ollama/Gemma4 edge case: completely empty response (no content, no tool_calls).
        # Retry up to 2 times with an explicit nudge injected.
        if not content and not tool_calls:
            nudge_attempts = 0
            while not content.strip() and not tool_calls and nudge_attempts < 2:
                nudge_attempts += 1
                _log.warning("[ollama_client] Empty response received, sending nudge (attempt %d/2)", nudge_attempts)
                if _has_tool_results:
                    nudge_text = "Please synthesize your final response now based on the tool results above."
                else:
                    nudge_text = "Please provide a response to the user's query."
                messages_with_nudge = list(messages) + [
                    {"role": "user", "content": nudge_text}
                ]
                resp2, _ = _run_local(model, messages_with_nudge, [], temperature=0.7, options=active_opts)
                content = resp2.message.content or ""
                tool_calls = resp2.message.tool_calls
                msg = resp2.message

        if not tool_calls:
            # Fallback: some models (gemma4 at temp=0) emit tool call JSON
            # as text content instead of structured tool_calls. Parse and execute.
            _available = {t["function"]["name"] for t in tools}
            parsed = _parse_text_tool_call(content, _available)
            if parsed:
                fn_name, fn_args = parsed
                call_id = f"call_{_round}_text"
                print(f"[tool/text] {fn_name}({str(fn_args)[:200]})", flush=True)
                check_aborted()
                result = execute_tool(fn_name, fn_args)
                if len(result) > 4000:
                    result = result[:3800] + f"\n... [truncated — {len(result)} chars total]"
                if fn_name in _SEARCH_TOOL_NAMES:
                    result = _premise_inject(user_message, result)
                result = wrap_external_output(fn_name, result)
                # Append a synthetic assistant tool-call + tool result, then continue
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "tool",
                        "name": fn_name,
                        "tool_call_id": call_id,
                        "content": result,
                    }
                )
                continue
            # Synthesis via blocking (_has_tool_results): stream content to client now.
            if on_token and _has_tool_results and content:
                on_token(content)
            return content

        messages.append(msg)

        # Emit a progress label for each tool call before blocking execution begins,
        # so the UI / Telegram shows immediate feedback instead of a frozen bubble.
        if on_token:
            for call in tool_calls:
                label = _TOOL_PROGRESS_LABELS.get(
                    call.function.name, f"⚙️ Running {call.function.name}…"
                )
                on_token(f"\n\n{label}")

        # Parallelize tool execution for this round. Cap the pool so fast IO tools still
        # overlap, but model-invoking tools are serialized via the semaphore in _run_tool_gated.
        # NOTE: plain ThreadPoolExecutor + future.result() has NO timeout — a hung tool
        # (stuck subprocess, dead socket) would freeze the whole round forever. Bound
        # each wait (tool round timeout) and never block pool shutdown on stragglers.
        check_aborted()
        tool_timeout = _TOOL_ROUND_TIMEOUT_S
        executor = ThreadPoolExecutor(max_workers=min(len(tool_calls), 4))
        try:
            future_to_idx: dict = {}
            for i, call in enumerate(tool_calls):
                # copy_context() snapshots per-request contextvars (project root,
                # run-id) from this thread; executor workers start with a fresh
                # default context otherwise, which would drop the project root.
                _ctx = contextvars.copy_context()
                future_to_idx[
                    executor.submit(_ctx.run, _run_tool_gated, call.function.name, call.function.arguments)
                ] = i
            results_map = {}
            for future, idx in future_to_idx.items():
                try:
                    results_map[idx] = future.result(timeout=tool_timeout)
                except concurrent.futures.TimeoutError:
                    results_map[idx] = (
                        f"[error] tool execution timed out after {tool_timeout}s: "
                        f"{tool_calls[idx].function.name}"
                    )
                except Exception as exc:
                    results_map[idx] = f"[error] tool execution failed: {exc}"
        finally:
            executor.shutdown(wait=False)

        for i, call in enumerate(tool_calls):
            fn_name = call.function.name
            call_id = f"call_{_round}_{i}"
            print(f"[tool] {fn_name}({str(call.function.arguments)[:200]})", flush=True)
            result = results_map[i]

            _is_error = is_error_result(result)
            consecutive_errors = consecutive_errors + 1 if _is_error else 0

            if run_id:
                _record_trace(run_id, {
                    "run_id": run_id, "tool": fn_name,
                    "args": {k: str(v)[:80] for k, v in (call.function.arguments or {}).items()},
                    "result_summary": result[:120], "elapsed_ms": None, "error": _is_error,
                })

            # Truncate large tool results to prevent context blowup across rounds
            if len(result) > 4000:
                result = result[:3800] + f"\n... [truncated — {len(result)} chars total]"

            # Inject contradiction warning for search tools before model sees result
            if fn_name in _SEARCH_TOOL_NAMES:
                result = _premise_inject(user_message, result)

            # Delimit externally-sourced content (search/file/email/browser/OCR) so the
            # model treats it as reference data, not instructions — before any of our own
            # pipeline hints below get appended (those are trusted, self-generated text).
            result = wrap_external_output(fn_name, result)

            if is_checkpoint_result(fn_name, result):
                return result

            # Round-trip logic (pipeline hints, browser auto-actions, etc.)
            _tool_names_available = {t["function"]["name"] for t in tools}
            if fn_name == "get_latest_email" and "send_telegram" in _tool_names_available:
                result += (
                    "\n\n[Next step: call send_telegram(message=<your concise plain-text summary "
                    "of the above email, under 100 words>)]"
                )
            if fn_name == "read_file" and "send_telegram" in _tool_names_available:
                result += (
                    "\n\n[Pipeline: file loaded. Next step: call send_telegram(message=<your "
                    "concise plain-text summary, under 200 words>).]"
                )
            if fn_name == "list_pdf_attachments" and "send_telegram" in _tool_names_available:
                result += (
                    "\n\n[SYSTEM INSTRUCTION: You MUST call the send_telegram tool RIGHT NOW. "
                    "Do not write any text response. Your next action must be a send_telegram tool call "
                    "with a plain-text summary of the PDFs listed above (sender names + filenames, under 150 words). "
                    "Calling send_telegram is mandatory — do not skip it.]"
                )
            if fn_name == "take_screenshot":
                # Extract the file path from the result string and run OCR on it immediately.
                # Gemma4 is a text-only model — without this it hallucinates screen content.
                import re as _re
                _path_match = _re.search(r"Screenshot saved:\s*(\S+\.png)", result)
                if _path_match:
                    _img_path = _path_match.group(1)
                    try:
                        _ocr_result = execute_tool("ocr_image", {"image_path": _img_path})
                        result += (
                            f"\n\n[OCR text extracted from screenshot — this is the ACTUAL text on screen]:\n{_ocr_result}\n"
                            f"[Describe what you see based on the above OCR text only. Do NOT invent or guess content.]"
                        )
                    except Exception as _e:
                        result += f"\n\n[OCR failed: {_e}. Describe only that a screenshot was taken at {_img_path}.]"
                else:
                    result += "\n\n[Screenshot path could not be parsed. Report only what the tool returned above.]"
            if fn_name == "detect_pdf_form_fields" and "fill_pdf_form" in _tool_names_available:
                result += (
                    "\n\n[Next step: call fill_pdf_form with the field values you want to set. "
                    'Example: fill_pdf_form(path=<same path>, fields=\'{"FieldName": "value", ...}\')]'
                )
            if fn_name == "detect_form_fields" and "fill_form" in _tool_names_available:
                result += (
                    "\n\n[Next step: call fill_form with the field values you want to set. "
                    'Use field names from the detection result. Example: fill_form(file_path=<same path>, fields=\'{"field_name": "value", ...}\')]'
                )
            if fn_name == "fill_form" and "update_form" in _tool_names_available:
                result += (
                    "\n\nTip: To update specific fields in this form later, use update_form() instead of filling the entire form again."
                )
            messages.append(
                {
                    "role": "tool",
                    "name": fn_name,
                    "tool_call_id": call_id,
                    "content": result,
                }
            )

        if consecutive_errors >= 3:
            logging.getLogger(__name__).warning(
                "[ollama_client] %d consecutive tool errors on %s, stopping early",
                consecutive_errors, model,
            )
            messages.append({
                "role": "user",
                "content": "You've hit repeated tool errors. Stop trying more tool calls "
                           "and summarize what you found so far, noting the errors.",
            })
            break
    if tool_calls:
        check_aborted()
        resp = _get_client().chat(
            model=model,
            messages=messages,
            tools=None,
            keep_alive=-1,
            options=active_opts or None,
            think=_think,
        )
        content = resp.message.content or ""
        if on_token and content:
            on_token(content)
        return content

    return content
