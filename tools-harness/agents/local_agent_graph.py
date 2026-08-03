"""
LangGraph local-agent: graph construction and compilation.

Builds the ReAct agent graph with:
- agent node (LLM + tool calling)
- tool node (execute tool calls)
- conditional edges for loop vs. termination
- step count guard to prevent infinite loops
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from agents.local_agent_state import LocalAgentState
from clients.cloud_client import DEFAULT_CLOUD_MODEL, CLOUD_FALLBACK_MODEL, is_cloud_model
from store.trace_store import get_trace, store_trace as _store_trace, record as _record_trace
from log_config import setup_logging as _setup_logging

_log = _setup_logging("local_agent_graph")

# Code tasks (multi-file read/edit/verify cycles cost 2-3 steps each) need more
# headroom than the 15-step default tuned for document/form tasks.
# document raised 15->25: markdown->PDF->email-attachment chains routinely
# blew the 15/17 cap (recursion abort confirmed live 2026-07-09 10:42:28).
_DEFAULT_MAX_STEPS = 15
_MAX_STEPS_BY_TASK = {"code": 50, "document": 35}


def _max_steps_for(task: str) -> int:
    return _MAX_STEPS_BY_TASK.get(task, _DEFAULT_MAX_STEPS)


def should_continue_with_error_check(state: LocalAgentState) -> Literal["tools", "end"]:
    """
    Enhanced routing that also checks error count.
    If too many consecutive errors, terminate to avoid loops.
    """
    # Check error threshold
    if state.error_count >= 3:
        _log.warning(
            "[local-agent/graph] too many consecutive errors (%d), forcing end",
            state.error_count,
        )
        return "end"

    # Check step limit
    if state.step_count >= state.max_steps:
        _log.warning(
            "[local-agent/graph] step limit reached (%d >= %d), forcing end",
            state.step_count,
            state.max_steps,
        )
        return "end"

    # Check if last message has tool calls
    messages = state.messages
    if not messages:
        return "end"

    from langchain_core.messages import AIMessage
    last_message = messages[-1]

    if isinstance(last_message, AIMessage) and getattr(last_message, "tool_calls", None):
        return "tools"

    return "end"


def should_continue_after_verify(state: LocalAgentState) -> Literal["agent", "end"]:
    """After verify_answer runs: retry once on a flagged answer, else terminate."""
    if state.verified:
        return "end"
    return "agent"


def create_local_agent_graph():
    """
    Create and compile the local-agent LangGraph graph.

    Returns:
        Compiled graph ready for invocation/streaming
    """
    workflow = StateGraph(LocalAgentState)

    # Add nodes
    from agents.local_agent_nodes import call_model, tool_node, plan_step, verify_answer
    # Per-node timeouts (langgraph 1.2): requires async nodes — kill a hung tool
    # call (e.g. a stuck bash_exec) instead of letting the whole run hang. The
    # model client already enforces a 120s timeout; 180s here is a hard ceiling.
    # Retry transient model-call failures (e.g. DeepSeek/network blips) up to 3x
    # with exponential backoff. Timeouts are excluded — a node that was killed for
    # hanging must NOT be retried (that would just burn another 180s).
    _agent_retry = RetryPolicy(
        max_attempts=3,
        retry_on=lambda e: "Timeout" not in type(e).__name__,
    )
    workflow.add_node("plan", plan_step)
    workflow.add_node("agent", call_model, timeout=180, retry_policy=_agent_retry)
    workflow.add_node("tools", tool_node, timeout=120)
    workflow.add_node("verify", verify_answer)

    # Set entry point — plan runs once before the agent loop starts
    workflow.set_entry_point("plan")
    workflow.add_edge("plan", "agent")

    # Add conditional edges from agent — a tool-call-free answer now goes to
    # verify, not straight to END (see should_continue_after_verify below)
    workflow.add_conditional_edges(
        "agent",
        should_continue_with_error_check,
        {
            "tools": "tools",
            "end": "verify",
        },
    )

    # After tools, always go back to agent
    workflow.add_edge("tools", "agent")

    # After verify: retry once on a flagged answer, else terminate
    workflow.add_conditional_edges(
        "verify",
        should_continue_after_verify,
        {
            "agent": "agent",
            "end": END,
        },
    )

    # Compile with recursion limit
    graph = workflow.compile()

    _log.info("[local-agent/graph] graph compiled successfully")
    return graph


# Singleton instance for reuse
_local_agent_graph = None


def get_local_agent_graph():
    """Get or create the compiled local-agent graph (singleton)."""
    global _local_agent_graph
    if _local_agent_graph is None:
        _local_agent_graph = create_local_agent_graph()
    return _local_agent_graph


def run_local_agent(
    query: str,
    model: str = DEFAULT_CLOUD_MODEL,
    chat_id: str | None = None,
    max_steps: int | None = None,
    stream_callback=None,
    task: str = "full",
):
    """
    Run the local-agent graph with a user query.

    Args:
        query: User's input message
        model: Model to use (gemma4, mistral-nemo, etc.)
        chat_id: Optional chat session ID
        max_steps: Maximum agent-tool loop iterations. Defaults per-task via
            _max_steps_for() (task="code" gets more headroom) when not passed explicitly.
        stream_callback: Optional callback for streaming updates
        task: Toolset mode — "full" (default), "code", or "document"

    Returns:
        Final response string
    """
    if max_steps is None:
        max_steps = _max_steps_for(task)
    from langchain_core.messages import HumanMessage, AIMessage
    from store.conversation import get as conv_get, trim_to_budget

    messages = []
    if chat_id:
        raw_history = conv_get(chat_id)
        trimmed_history = trim_to_budget(raw_history, model, query)
        for turn in trimmed_history:
            role = turn.get("role")
            content = turn.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

    if max_steps is None:
        max_steps = _max_steps_for(task)

    messages.append(HumanMessage(content=query))

    graph = get_local_agent_graph()

    initial_state = {
        "messages": messages,
        "step_count": 0,
        "max_steps": max_steps,
        "current_model": model,
        "chat_id": chat_id,
        "error_count": 0,
        "task": task,
        "verified": False,
        "verify_attempts": 0,
    }

    config = {"recursion_limit": max_steps + 2}  # +2 for safety margin

    _log.info("[local-agent/run] query=%s model=%s", query[:50], model)

    if stream_callback:
        # Streaming mode — use the async path so per-node timeouts are honored.
        from langchain_core.messages import AIMessage

        async def _astream():
            final_response = ""
            async for chunk in graph.astream(
                initial_state,
                config,
                stream_mode="values",
            ):
                messages = chunk.get("messages", [])
                if messages:
                    last_msg = messages[-1]
                    # Gated on verified, same reasoning as run_local_agent_streaming —
                    # a tool-call-free AIMessage may still be awaiting the verify retry.
                    if (isinstance(last_msg, AIMessage) and not getattr(last_msg, "tool_calls", None)
                            and chunk.get("verified")):
                        final_response = last_msg.content
                        stream_callback(last_msg.content)
            return final_response

        return asyncio.run(_astream())

    # Non-streaming mode — async path for node-timeout support.
    # A node timeout (or any failure) returns a clean message rather than
    # raising into the caller — the silent-hang guard.
    try:
        result = asyncio.run(graph.ainvoke(initial_state, config))
    except Exception as e:  # noqa: BLE001
        _log.error("[local-agent/run] query=%s aborted: %s", query[:50], e)
        return f"Sorry — I couldn't finish that (the local agent timed out or errored: {e})."
    messages = result.get("messages", [])

    # Extract the final AI message content
    from langchain_core.messages import AIMessage
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            return msg.content

    return "No response generated."


def run_local_agent_streaming(
    query: str, model: str = DEFAULT_CLOUD_MODEL, chat_id: str | None = None, task: str = "full",
):
    """
    Run the local-agent graph with SSE streaming + specialist pre-dispatch.

    If the query matches a specialist domain (e.g. path discovery), the
    specialist runs first. Its results are yielded as specialist_start /
    specialist_done events AND injected into the conversation so the main
    agent starts with resolved paths.

    Yields tuples of (event_type, payload_dict):
        - "specialist_start": A specialist is being invoked
        - "specialist_done": Specialist completed (paths, summary, etc.)
        - "tool_call": Tool is being called (main agent)
        - "tool_result": Tool returned a result (main agent)
        - "final": Final response from main agent
    """
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

    # Generated up front (not just before graph.stream) so specialist trace entries
    # below share the same run_id as the graph run that follows them.
    run_id = uuid.uuid4().hex[:8]

    # --- Specialist pre-dispatch (unified 9-specialist router) ---
    from agents.specialists.dispatch import dispatch as specialist_dispatch
    from agents.specialists.path_specialist import PathResult
    from agents.specialists.read_specialist import ReadResult
    from agents.specialists.data_specialist import DataResult
    from agents.specialists.form_specialist import FormResult
    from agents.specialists.audio_specialist import AudioResult

    dispatch_result = specialist_dispatch(query, model=model)

    known_paths: list[str] = []
    read_text: str = ""
    audio_transcript: str = ""
    form_fields: dict = {}
    data_text: str = ""
    data_chart_path: str = ""

    if dispatch_result:
        yield dispatch_result.events["start"]
        yield dispatch_result.events["done"]

        specialist = dispatch_result.specialist
        result = dispatch_result.result

        # Emit the specialist's internal tool calls so callers (E2E tests, trace) see them —
        # every specialist result now flows to the main agent for synthesis (no short-circuit
        # returns below), so this is no longer path-only.
        _tool_trace = getattr(result, "tool_trace", None)
        if _tool_trace:
            for tool_name in _tool_trace:
                yield ("tool_call", {"name": tool_name, "args": {}})
                _record_trace(run_id, {
                    "run_id": run_id, "tool": tool_name, "args": {},
                    "result_summary": f"[specialist:{specialist}]", "elapsed_ms": 0, "error": False,
                })

        # --- Extract context for cross-specialist memory; the main agent always
        # synthesizes the final reply from this context, never a raw passthrough. ---
        if specialist == "path" and isinstance(result, PathResult) and result.paths:
            known_paths = result.paths
        elif specialist == "read" and isinstance(result, ReadResult):
            if result.path:
                known_paths.append(result.path)
            read_text = result.text
        elif specialist == "audio" and isinstance(result, AudioResult):
            audio_transcript = result.transcript
        elif specialist == "form" and isinstance(result, FormResult):
            form_fields = result.fields_detected
        elif specialist == "data" and isinstance(result, DataResult):
            data_text = result.text
            data_chart_path = result.chart_path or ""

    # --- Build augmented query with known context ---
    augmented_query = query
    context_parts = []

    if dispatch_result and dispatch_result.specialist == "path" and not known_paths:
        r = dispatch_result.result
        summary = r.summary if isinstance(r, PathResult) else str(r)
        context_parts.append(f"[Path specialist searched but found nothing: {summary}. Try alternative strategies.]")

    if known_paths:
        path_str = "\n".join(f"  - {p}" for p in known_paths[:10])
        extra = f"\n  ... and {len(known_paths) - 10} more" if len(known_paths) > 10 else ""
        context_parts.append(f"[Known file paths:\n{path_str}{extra}]")
    if read_text:
        preview = read_text[:2000]
        suffix = f"\n... ({len(read_text)} chars total)" if len(read_text) > 2000 else ""
        context_parts.append(f"[File content already read:\n{preview}{suffix}]")
    if audio_transcript:
        preview = audio_transcript[:2000]
        suffix = f"\n... ({len(audio_transcript)} chars total)" if len(audio_transcript) > 2000 else ""
        context_parts.append(f"[Audio transcript already available:\n{preview}{suffix}]")
    if form_fields:
        context_parts.append(f"[Form fields detected: {json.dumps(form_fields)}]")
    if data_text:
        preview = data_text[:2000]
        suffix = f"\n... ({len(data_text)} chars total)" if len(data_text) > 2000 else ""
        context_parts.append(f"[Data analysis already run:\n{preview}{suffix}]")
        if data_chart_path:
            context_parts.append(f"[Chart already generated: {data_chart_path}]")

    if context_parts:
        augmented_query = f"{query}\n\n" + "\n".join(context_parts)

    from langchain_core.messages import HumanMessage, AIMessage
    from store.conversation import get as conv_get, trim_to_budget

    messages = []
    if chat_id:
        raw_history = conv_get(chat_id)
        trimmed_history = trim_to_budget(raw_history, model, query)
        for turn in trimmed_history:
            role = turn.get("role")
            content = turn.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

    messages.append(HumanMessage(content=augmented_query))

    graph = get_local_agent_graph()

    _max_steps = _max_steps_for(task)
    initial_state = {
        "messages": messages,
        "step_count": 0,
        "max_steps": _max_steps,
        "current_model": model,
        "chat_id": chat_id,
        "error_count": 0,
        "run_id": run_id,
        "trace": [],
        "task": task,
        "verified": False,
        "verify_attempts": 0,
    }

    config = {"recursion_limit": _max_steps + 2}

    _log.info("[local-agent/stream] run_id=%s query=%s model=%s", run_id, augmented_query[:50], model)

    final_trace: list[dict] = []

    # Bridge the async graph stream (required for per-node timeouts) to this
    # sync generator so the SSE caller is unchanged. The async run executes in a
    # daemon thread with its own event loop; chunks are pushed to a queue and
    # yielded here as they arrive.
    _q: "queue.Queue" = queue.Queue()
    _SENTINEL = object()

    async def _arun():
        try:
            async for chunk in graph.astream(initial_state, config, stream_mode="values"):
                _q.put(chunk)
        except Exception as e:  # noqa: BLE001 — surface as a final event below
            _q.put(("__error__", e))
        finally:
            _q.put(_SENTINEL)

    def _pump():
        asyncio.run(_arun())

    _thread = threading.Thread(target=_pump, daemon=True)
    _thread.start()

    # A raise inside call_model (e.g. the ollama client's 120s timeout on a
    # stalled gemma4 call, or a node-level timeout firing) would otherwise
    # escape as an unhandled exception and leave the caller with no "final"
    # event — the exact silent hang we're fixing. Turn any failure into a clean
    # final message the user actually sees.
    try:
        while True:
            item = _q.get()
            if item is _SENTINEL:
                break
            if isinstance(item, tuple) and item and item[0] == "__error__":
                raise item[1]

            chunk = item
            messages = chunk.get("messages", [])
            if not messages:
                continue

            # Accumulate trace from each state update
            if chunk.get("trace"):
                final_trace = chunk["trace"]

            last_msg = messages[-1]

            if isinstance(last_msg, AIMessage):
                if getattr(last_msg, "tool_calls", None):
                    # Tool call
                    for tc in last_msg.tool_calls:
                        # tc can be dict or ToolCall object
                        name = tc["name"] if isinstance(tc, dict) else getattr(tc, "name", "")
                        args = tc["args"] if isinstance(tc, dict) else getattr(tc, "args", {})
                        yield ("tool_call", {"name": name, "args": args})
                elif chunk.get("verified"):
                    # Final response — gated on the verify node's approval, not just
                    # "no tool calls", so a flagged-and-retried answer never gets
                    # streamed to the client as final before the retry completes.
                    _log.info("[local-agent/stream] run_id=%s done trace_len=%d",
                              run_id, len(final_trace))
                    if final_trace:
                        _store_trace(run_id, final_trace)
                    yield ("final", last_msg.content)
                # else: tool-call-free AIMessage still awaiting verify — no event yet.

            elif isinstance(last_msg, ToolMessage):
                yield ("tool_result", {
                    "name": last_msg.name,
                    "content": last_msg.content[:500],  # Truncate for SSE
                })
    except Exception as e:
        _log.error("[local-agent/stream] run_id=%s aborted: %s", run_id, e)
        yield ("final", f"Sorry — I couldn't finish that (the local agent timed out or errored: {e}).")
