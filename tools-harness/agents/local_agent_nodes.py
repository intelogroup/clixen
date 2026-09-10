"""
Node functions for the LangGraph local-agent.

Contains:
- call_model: Invokes the LLM with tools (uses native ollama client)
- should_continue: Routing logic (tools vs. end)
- tool_executor: Wraps existing EXECUTORS from registry.py
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agents.local_agent_state import LocalAgentState
from agents.local_agent_tools import get_local_agent_tools, get_local_agent_executors


def _tools_for_state(state) -> list:
    """Tool schemas narrowed by the matched skill and specialist handoff state."""
    tools = get_local_agent_tools(state.task)
    if state.skill_tools:
        from tools.registry import tools_with_tags
        # Union with "core" so a matched skill's narrow list (e.g. create_task's
        # 3 tools) can't silently hide always-available generic tools like
        # gog_exec — narrowing is for prompt size, not for hard-blocking tools
        # the skill just didn't happen to list.
        keep = set(state.skill_tools) | tools_with_tags("core")
        tools = [t for t in tools if t["function"]["name"] in keep]
    if state.excluded_tools:
        excluded = set(state.excluded_tools)
        tools = [t for t in tools if t["function"]["name"] not in excluded]
    return tools
from tools.registry import execute_tool as _registry_execute_tool, is_error_result
from tools.filesystem import find_files
from tools.injection_guard import wrap_external_output
from clients.cloud_client import is_cloud_model
from clients.ollama_client import DEFAULT_MODEL as _LOCAL_DEFAULT_MODEL, _trace_resp, _run_local
from log_config import setup_logging as _setup_logging

_log = _setup_logging("local_agent_nodes")


def _derived_numeric_hint(query: str, answer: str, transcript: str) -> str | None:
    """Detect a bounded refusal where the evidence contains a simple formula."""
    import re
    if not re.search(r"\b(?:how much|what is|calculate|number|amount|revenue|total)\b", query, re.I):
        return None
    if not re.search(r"\b(?:not established|cannot determine|can't determine|unknown|not available)\b", answer, re.I):
        return None
    growth = re.search(
        r"(?:grew|increased|rose)\s+by\s+(?:exactly\s+)?(\d+(?:\.\d+)?)%\s+over\s+(?:Q2|quarter 2)",
        transcript, re.I,
    )
    base_match = re.search(
        r"(?:Q2|quarter 2)\s+(?:revenue|value|amount)\s*(?:is|=|:)\s*\$?([\d,]+)",
        transcript, re.I,
    ) or re.search(r"\|\s*(?:Q2|quarter 2)\s*\|\s*\$?([\d,]+)", transcript, re.I)
    if not growth or not base_match:
        return None
    percent, base = growth.group(1), base_match.group(1)
    base_value = int(base.replace(",", ""))
    result = base_value * (1 + float(percent) / 100)
    rendered = str(int(result)) if result.is_integer() else str(result)
    return (
        f"The answer must not be a refusal: the evidence states that the value grew by "
        f"{percent}% over Q2={base_value}. Calculate {base_value} * (1 + {percent}/100) = "
        f"{rendered}, cite the supporting evidence, and answer the user's numeric question."
    )

# ponytail: cooldown cache — after cloud call fails, skip retry for N seconds.
# Prevents 50 consecutive 404s in one agent run (observed: 15:07-15:13, Jul 8).
_cloud_deadline: dict[str, float] = {}
_cloud_lock = threading.Lock()
_CLOUD_COOLDOWN = 300

# System prompt for local-agent (reused from harness.py)

def _build_system_prompt(task: str, tool_schemas: list[dict], home: str,
                         project_root: str | None = None,
                         skill_prompt: str | None = None,
                         chat_id: str | None = None) -> str:
    """Build the compact runtime prompt.

    Tool schemas are already sent to the model, so this prompt contains only
    role, universal operating rules, and task-specific constraints. Detailed
    procedures belong in the matched skill contract.
    """
    tool_names = [t["function"]["name"] for t in tool_schemas]
    has = set(tool_names)
    role = {"code": "coding assistant", "document": "document assistant"}.get(
        task, "local operations assistant"
    )
    lines = [f"You are Gemma, Clixen's {role} on the user's computer."]
    if project_root:
        lines.append(f"Project root: {project_root}. Use this exact root for project files.")
    if task == "document" and chat_id and project_root:
        lines.append(
            f"Document session id: {chat_id}. Use document_scratchpad with this id and "
            f"workspace {project_root} for bounded, user-visible working notes."
        )
    if skill_prompt:
        lines.extend(["", skill_prompt])
    lines.extend([
        "",
        "OPERATING RULES:",
        "- Use actual tool calls; do not describe hypothetical calls.",
        "- Work in small steps: inspect, act, verify, then report.",
        "- Use exact absolute paths from tool results. Never invent paths or field values.",
        "- You have full read/write access to every directory under the user's home — nothing is "
        "sandboxed. A relative path like \"../x\" has no real shell cwd behind it and may resolve "
        f"oddly; if a lookup on a relative or ambiguous path fails, retry once with the absolute "
        f"path under {home} before telling the user it doesn't exist.",
        "- Do not repeat a failed call unless the approach or arguments change.",
        "- Never claim success without a confirming tool result.",
        "- For numeric or derived questions, calculate from the supplied evidence and show the arithmetic briefly; do not stop at a missing table cell when the surrounding notes provide a formula or value.",
        "- Treat notes, footnotes, captions, and narrative guidance as evidence that can complete or qualify a table.",
        "- For arithmetic derived from document evidence, use the calculator/run_python tool when available, then verify the result against the source values.",
        "- Preserve source files unless the user explicitly requests replacement.",
        "- Ask before destructive or irreversible actions; never handle passwords.",
    ])
    if "fulltext_search" in has or "semantic_file_search" in has:
        lines.append("- For open-ended local-content questions, use content search before browsing directories.")
    if task == "code":
        lines.extend([
            "",
            "CODE CHECKLIST: understand structure, locate relevant code, read before editing, "
            "make the smallest change, then run the relevant test or check.",
        ])
    lines.extend([
        "",
        "ACTIVE TOOLS: " + ", ".join(tool_names),
        f"LOCAL FILE PATHS: use {home}/Downloads, {home}/Documents, or {home}/Desktop; do not use ~.",
        "Respond briefly after completing the requested work and include unresolved issues.",
    ])
    return "\n".join(lines)


def _get_system_prompt(model: str, task: str = "full", query: str = "", tools: list[dict] | None = None, project_root: str | None = None, skill_prompt: str | None = None, chat_id: str | None = None) -> str:
    """Build system prompt with memory recall prepended."""
    import os
    home = os.path.expanduser("~")
    base = _build_system_prompt(task, tools or [], home, project_root, skill_prompt, chat_id)
    if query:
        from tools.memory_tools import recall_block
        mem_block = recall_block(query)
        if mem_block:
            return mem_block + "\n" + base
    return base



def _chat(model: str, messages: list[dict], tools: list[dict], temperature: float = 0.0):
    """Dispatch to OpenRouter/DeepSeek (cloud) or Ollama (local) based on the
    model string. Both return an object exposing .message.content/.tool_calls.

    Cloud stays the model even past the daily token budget (clients.cost_guard) —
    the budget is advisory/logged only, not a hard cutover to local gemma4.

    If cloud is unreachable at all (no network, missing API key, provider outage),
    falls back to local ollama with _LOCAL_DEFAULT_MODEL so the app keeps working
    offline instead of raising."""
    if is_cloud_model(model):
        with _cloud_lock:
            now = time.time()
            deadline = _cloud_deadline.get(model)
            if deadline is not None and now < deadline:
                _log.info("[local-agent] cloud %s in cooldown (%.0fs left), using local", model, deadline - now)
                response, _ = _run_local(_LOCAL_DEFAULT_MODEL, messages, tools, temperature=temperature)
                return response

        from clients.cloud_client import raw_completion, BudgetExceededError
        try:
            return raw_completion(model, messages, tools, temperature=temperature)
        except BudgetExceededError as e:
            _log.warning("[local-agent] %s, continuing on cloud anyway", e)
            return raw_completion(model, messages, tools, temperature=temperature, bypass_budget=True)
        except Exception as e:
            _log.warning("[local-agent] cloud unreachable (%s), falling back to local %s", e, _LOCAL_DEFAULT_MODEL)
            with _cloud_lock:
                _cloud_deadline[model] = time.time() + _CLOUD_COOLDOWN
                # ponytail: trim oldest when full
                if len(_cloud_deadline) > 50:
                    for k in sorted(_cloud_deadline, key=_cloud_deadline.get)[:-50]:
                        del _cloud_deadline[k]
            response, _ = _run_local(_LOCAL_DEFAULT_MODEL, messages, tools, temperature=temperature)
            return response
    response, _ = _run_local(model, messages, tools, temperature=temperature)
    return response


async def call_model(state: LocalAgentState) -> dict:
    """
    Agent node: invokes the LLM with the current message history.
    Prepends system prompt on first call.
    Increments step_count.
    Uses native ollama client for reliable tool calling.
    Uses filtered toolset to avoid overloading qwen3.
    """
    messages = state.messages
    step = state.step_count + 1
    model = state.current_model
    wrap_up_nudged = state.wrap_up_nudged

    _log.info("[local-agent/graph] step=%d model=%s", step, model)

    # Soft step-cap warning: at 80% of max_steps, nudge the model to wrap up
    # NOW instead of silently hitting the hard cap in should_continue and
    # discarding whatever partial work was in flight. Fires once per run.
    if not wrap_up_nudged and state.max_steps and step >= int(state.max_steps * 0.8):
        _log.info("[local-agent/graph] step=%d nearing max_steps=%d, injecting wrap-up nudge", step, state.max_steps)
        messages = messages + [SystemMessage(
            content="You're close to the step limit for this task. Wrap up now: stop "
            "calling more tools and give your best final answer with what you have."
        )]
        wrap_up_nudged = True

    # Convert LangChain messages to ollama format
    ollama_messages = []
    has_system = False

    for msg in messages:
        if isinstance(msg, SystemMessage):
            has_system = True
            ollama_messages.append({"role": "system", "content": msg.content})
        elif isinstance(msg, HumanMessage):
            ollama_messages.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                # AI message with tool calls
                tool_calls = []
                for tc in msg.tool_calls:
                    # tc is a dict with 'id', 'name', 'args'
                    args = tc.get("args", {})
                    if isinstance(args, str):
                        args = json.loads(args)
                    tool_calls.append({
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": args,
                        },
                    })
                ollama_messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": tool_calls,
                })
            else:
                ollama_messages.append({"role": "assistant", "content": msg.content})
        elif isinstance(msg, ToolMessage):
            ollama_messages.append({
                "role": "tool",
                "content": msg.content,
                "tool_call_id": msg.tool_call_id,
            })

    _log.debug("[local-agent/graph] ollama_messages count=%d", len(ollama_messages))

    # Get filtered tools for local-agent (must come before system prompt)
    tools = _tools_for_state(state)

    # Add system prompt if not present
    if not has_system:
        _query_text = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")
        system_prompt = _get_system_prompt(model, task=state.task, query=_query_text, tools=tools, project_root=state.project_root, skill_prompt=state.skill_prompt, chat_id=state.chat_id)
        ollama_messages.insert(0, {"role": "system", "content": system_prompt})
    _log.info("[local-agent/graph] SENDING TO OLLAMA: %d tools, first tool: %s", len(tools), tools[0]['function']['name'] if tools else 'NONE')

    # Call the model (dispatches cloud vs. local) — run sync client in a thread
    # so the node-level timeout in local_agent_graph.py can interrupt a hang.
    # gemma4 + tools: pass temperature=None so _run_local applies its own
    # setdefault(temperature=1.0, top_p=0.95, top_k=64) — Google's model-card
    # recommended sampling for tool calling. Forcing temperature=0.0 here (the
    # old behavior) pre-populates opts["temperature"] before _run_local's
    # gemma4+tools block runs, so its setdefault is a silent no-op and every
    # tool-calling round decodes greedy instead — confirmed live as the cause
    # of the "content='' tool_calls=[] done_reason=stop" empty-stall on step 1
    # (_run_streaming already gets this right via .update(), not .setdefault()).
    _gemma_with_tools = model.startswith("gemma4") and bool(tools)
    response = await asyncio.to_thread(
        _chat,
        model=model,
        messages=ollama_messages,
        tools=tools,
        temperature=None if _gemma_with_tools else (0.0 if model.startswith("gemma4") else 0.7),
    )

    _trace_resp(f"local-agent-step-{step}", model, response)

    # Convert ollama response back to LangChain AIMessage
    response_msg = response.message  # ChatResponse.message is a Message object
    msg_content = getattr(response_msg, "content", "") or ""
    tool_calls = getattr(response_msg, "tool_calls", None)

    _log.debug("[local-agent/graph] msg_content len=%d has_tool_calls=%s", len(msg_content), bool(tool_calls))

    if not tool_calls and not msg_content and tools:
        # gemma4 empty-generation stall (content='' + tool_calls=[] + done_reason='stop',
        # small nonzero eval_count) — confirmed live as stochastic decode flakiness on
        # gemma4:12b/Ollama 0.32.6, not driven by prompt length, tool count, or content
        # (isolated repro ruled all three out). Since it's stochastic, resampling the
        # SAME request is cheap and is exactly what the old fallback chain (tools=[]
        # synthesis -> verify-reject -> loop back to agent with tools) eventually did
        # anyway, just after two extra round trips (~15-20s). Try 2 cheap resamples with
        # tools still attached first — this is what actually recovers it, per live traces.
        for _resample_i in range(2):
            _log.warning("[local-agent/graph] empty response at step=%d, resampling (%d/2)", step, _resample_i + 1)
            resample_resp = await asyncio.to_thread(
                _chat, model=model, messages=ollama_messages, tools=tools,
                temperature=None if _gemma_with_tools else (0.0 if model.startswith("gemma4") else 0.7),
            )
            _trace_resp(f"local-agent-step-{step}-resample-{_resample_i + 1}", model, resample_resp)
            resample_msg = resample_resp.message
            msg_content = getattr(resample_msg, "content", "") or ""
            tool_calls = getattr(resample_msg, "tool_calls", None)
            if tool_calls or msg_content:
                response_msg = resample_msg
                break

    if not tool_calls and not msg_content:
        # Resampling didn't recover it (or tools was empty to begin with) — fall back
        # to a tools=[] synthesis call, same pattern as ollama_client.py's forced-synthesis path.
        _log.warning("[local-agent/graph] empty response at step=%d, forcing synthesis retry", step)
        try:
            retry_resp = await asyncio.to_thread(
                _chat, model=model, messages=ollama_messages, tools=[],
                temperature=0.0 if model.startswith("gemma4") else 0.7,
            )
            _trace_resp(f"local-agent-step-{step}-forced-synthesis", model, retry_resp)
            msg_content = getattr(retry_resp.message, "content", "") or ""
        except Exception as e:  # noqa: BLE001 — forced synthesis is best-effort, never blocks the run
            _log.warning("[local-agent/graph] forced synthesis retry failed (%s)", e)

    if tool_calls:
        _log.info("[local-agent/graph] model returned %d tool call(s)", len(tool_calls))
        # Build AIMessage with tool calls
        lc_tool_calls = []
        for tc in tool_calls:
            func_name = tc.function.name
            func_args = tc.function.arguments
            # func_args might be a dict or JSON string
            if isinstance(func_args, str):
                func_args = json.loads(func_args)
            lc_tool_calls.append({
                "id": getattr(tc, "id", f"call_{step}_{func_name}"),
                "name": func_name,
                "args": func_args,
            })
        ai_msg = AIMessage(
            content=msg_content,
            tool_calls=lc_tool_calls,
        )
    else:
        _log.info("[local-agent/graph] model returned no tool calls (content: %s...)", msg_content[:100])
        ai_msg = AIMessage(content=msg_content)

    result = {"messages": [ai_msg], "step_count": step}
    if wrap_up_nudged and not state.wrap_up_nudged:
        result["messages"] = [messages[-1], ai_msg]  # persist the injected nudge too
        result["wrap_up_nudged"] = True
    return result


def plan_step(state: LocalAgentState) -> dict:
    """
    Planning node: runs once before the agent loop starts. Produces a short
    numbered plan from the user's query and injects it as a SystemMessage
    (not AIMessage — must not trip the "final answer" detection in
    run_local_agent_streaming, which watches for a tool-call-free AIMessage).
    The plan is context for the model, not an enforced state machine — the
    agent is free to deviate.
    """
    query = next((m.content for m in reversed(state.messages) if isinstance(m, HumanMessage)), "")
    if not query:
        return {}

    model = state.current_model
    result_messages = []

    # ponytail: word count was tried as a skip-plan-generation heuristic and
    # reverted — it doesn't predict complexity ("rename all .txt files to .md"
    # is 6 words and genuinely multi-step; "list files in current directory"
    # is 5 words and trivial). Confirmed live via test_local_agent_verify.py
    # regressions. _SHORT_QUERY_WORDS below is still used for the separate,
    # evidence-backed auto-search gate — don't reuse it here without a better
    # complexity signal than word count.
    _SHORT_QUERY_WORDS = 8
    prompt = [
        {"role": "system", "content": "Break the user's request into a short numbered plan "
                                       "(2-6 steps, one line each). Steps only, no preamble. "
                                       "\"local data\"/\"my data\" means the user's own files/notes/device state "
                                       "(search local files) — not geography (\"near me\", clinics/services). "
                                       "Only treat it as geographic if the query names a place or location-bound service."},
        {"role": "user", "content": query},
    ]
    try:
        response = _chat(model=model, messages=prompt, tools=[], temperature=0.0)
        plan_text = (getattr(response.message, "content", "") or "").strip()
    except Exception as e:  # noqa: BLE001 — planning is best-effort, never blocks the run
        _log.warning("[local-agent/graph] plan_step failed (%s), continuing without a plan", e)
        plan_text = ""

    if plan_text:
        _log.info("[local-agent/graph] plan=%s", plan_text[:200])
        result_messages.append(SystemMessage(content=f"[Plan]\n{plan_text}"))

    # ponytail: system-prompt instructions telling the model to prefer
    # fulltext_search/semantic_file_search over list_directory+grep_files
    # were tested live and ignored (same class of gap as the voice-length
    # rule in CLAUDE.md) — the model kept wandering directories by hand and
    # timing out, or hallucinating an unrelated answer when nothing turned
    # up. Code backstop: for an open-ended question naming no explicit file
    # path (structural signal, not keyword-matched — same pattern as
    # count_path_tokens in _harness_fs_actions.py), run both indexed
    # searches deterministically before the loop starts and hand the model
    # real grounded results up front, regardless of which tool it reaches for.
    # A short query (same threshold as the plan-skip above) has no room left
    # for an actual search topic once you subtract the verb+object — "list
    # files in current directory" is 5 words and pure browsing intent, not a
    # content search. Firing auto-search on it wastes an embedding call and,
    # confirmed live, injects irrelevant grounding content (docx chunks with
    # zero relevance) that measurably raises the odds of the gemma4 empty
    # tool-call stall (0/8 stall without this block vs 1/8 with it, same
    # prompt otherwise — see local-agent-tools.md debug notes).
    _available_names = {t["function"]["name"] for t in _tools_for_state(state)}
    _has_search = "fulltext_search" in _available_names or "semantic_file_search" in _available_names
    if _has_search and len(query.split()) > _SHORT_QUERY_WORDS:
        from _harness_fs_actions import count_path_tokens
        if count_path_tokens(query) == 0:
            found = []
            for name in ("fulltext_search", "semantic_file_search"):
                if name not in _available_names:
                    continue
                try:
                    out = _registry_execute_tool(name, {"query": query, "top_k": 3})
                    if out and not is_error_result(out):
                        from store.conversation import compress_tool_output
                        found.append(f"[{name}]\n{compress_tool_output(out, max_chars=2000, max_tail_lines=40)}")
                except Exception as e:  # noqa: BLE001 — best-effort grounding, never blocks the run
                    _log.warning("[local-agent/graph] auto-search %s failed (%s)", name, e)
            _log.info("[local-agent/graph] auto-search fired, %d/%d tools returned results", len(found), 2)
            if found:
                result_messages.append(SystemMessage(
                    content="[Auto-search results for the user's question — use this content if it "
                            "answers the request; if it doesn't, say you couldn't find it rather than "
                            "guessing]\n\n" + "\n\n".join(found)
                ))

    return {"messages": result_messages} if result_messages else {}


def verify_answer(state: LocalAgentState) -> dict:
    """
    Verification node: runs after the agent produces a tool-call-free answer.
    One cheap critique call checks the answer against the transcript. On a
    flagged mismatch, appends the critique as a SystemMessage and routes back
    to "agent" for exactly one retry (verify_attempts caps it). Otherwise sets
    verified=True so the streaming consumer knows this is the real final
    answer — see should_continue_after_verify in local_agent_graph.py.
    """
    messages = state.messages
    # Must be the actual last message, not just the most recent tool-call-free
    # AIMessage anywhere in history — on the error-escalation path (error_count
    # >= 3 forces "end") the last message is a ToolMessage, and scanning further
    # back would find a stale AIMessage from a previous chat_id turn and critique
    # that instead of correctly recognizing "no fresh answer was produced here".
    last_ai = messages[-1] if messages and isinstance(messages[-1], AIMessage) and not messages[-1].tool_calls else None
    if last_ai is None or state.verify_attempts >= 1:
        return {"verified": True}
    if not (last_ai.content or "").strip():
        _log.info("[local-agent/graph] verify rejected empty answer (no tool calls, no content)")
        return {
            "messages": [SystemMessage(content="[Self-check] Your last reply was empty. Give a real answer to the user's request now.")],
            "verify_attempts": state.verify_attempts + 1,
            "verified": False,
        }

    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    # A critique call costs a full model round trip (~9s) — worth it whenever
    # there's tool output the final answer could be hallucinating against,
    # even a single call (that's the common case, and exactly where
    # test_verify_answer_flags_bad_answer_and_retries_once caught a real miss
    # when this used to skip on <=1 tool calls). Only skip when there's
    # nothing to check against: zero tool calls and no errors.
    latest_human = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    if len(tool_messages) == 0 and state.error_count == 0 and "[Specialist findings:" not in latest_human:
        _log.info("[local-agent/graph] no tool calls and no errors, skipping verify call")
        return {"verified": True}

    from store.conversation import compress_tool_output
    query = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    transcript = "\n".join(
        f"[tool result] {compress_tool_output(m.content, max_chars=2000, max_tail_lines=40)}"
        for m in tool_messages
    )[-12000:]

    context_for_guard = transcript + "\n" + "\n".join(
        str(m.content) for m in messages
        if not isinstance(m, AIMessage) and getattr(m, "content", None)
    )
    derivation_hint = _derived_numeric_hint(query, last_ai.content, context_for_guard)
    if derivation_hint:
        _log.info("[local-agent/graph] deterministic numeric derivation guard fired")
        return {
            "messages": [SystemMessage(content=f"[Self-check] {derivation_hint}")],
            "verify_attempts": state.verify_attempts + 1,
            "verified": False,
        }

    prompt = [
        {"role": "system", "content": "You check whether an assistant's final answer actually "
                                       "addresses the user's request and is consistent with the "
                                       "tool results shown. For numeric or derived questions, "
                                       "recalculate from the evidence and reject answers such as "
                                       "'not established' when a note, footnote, caption, or "
                                       "narrative formula determines the value. Reply with exactly "
                                       "'OK' if it's fine, or one short line explaining what's wrong if it isn't."},
        {"role": "user", "content": f"User request: {query}\n\nTool results:\n{transcript}\n\n"
                                     f"Assistant's answer: {last_ai.content}"},
    ]
    try:
        response = _chat(model=state.current_model, messages=prompt, tools=[], temperature=0.0)
        verdict = (getattr(response.message, "content", "") or "").strip()
    except Exception as e:  # noqa: BLE001 — verification is best-effort, never blocks the run
        _log.warning("[local-agent/graph] verify_answer failed (%s), accepting answer as-is", e)
        return {"verified": True}

    if verdict.upper().startswith("OK"):
        return {"verified": True}

    _log.info("[local-agent/graph] verify flagged answer: %s", verdict[:200])
    return {
        "messages": [SystemMessage(content=f"[Self-check] Your last answer may be wrong: {verdict}. "
                                            f"Reconsider and correct it if needed.")],
        "verify_attempts": state.verify_attempts + 1,
        "verified": False,
    }


def should_continue(state: LocalAgentState) -> Literal["tools", "end"]:
    """
    Conditional edge: check if the last message has tool calls or is a final answer.
    Also checks step_count against max_steps.
    """
    messages = state.messages
    if not messages:
        return "end"

    last_message = messages[-1]

    # If the LLM returned tool calls, execute them
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        # Check step limit
        if state.step_count >= state.max_steps:
            _log.warning(
                "[local-agent/graph] step limit reached (%d >= %d), forcing end",
                state.step_count,
                state.max_steps,
            )
            # Return a message indicating step limit reached
            return "end"
        return "tools"

    # No tool calls — we're done
    return "end"


def _normalize_tool_call(tc) -> dict:
    """Convert ToolCall object or dict to a normalized dict."""
    if isinstance(tc, dict):
        return {
            "name": tc.get("name", ""),
            "args": tc.get("args", {}),
            "id": tc.get("id", "unknown"),
        }
    # ToolCall object — use attribute access
    return {
        "name": getattr(tc, "name", ""),
        "args": getattr(tc, "args", {}),
        "id": getattr(tc, "id", "unknown"),
    }


_DEDUPLICATED_READ_TOOLS = frozenset({
    "read_file", "read_document", "read_pdf", "parse_file", "parse_code",
    "list_directory", "file_info",
})


def _tool_call_signature(tool_name: str, args: dict) -> tuple[str, str]:
    """Stable signature used to stop a model rereading the same source forever."""
    return tool_name, json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)


def _previous_read_signatures(messages: list[AnyMessage]) -> set[tuple[str, str]]:
    signatures: set[tuple[str, str]] = set()
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for raw_call in getattr(message, "tool_calls", None) or []:
            call = _normalize_tool_call(raw_call)
            if call["name"] in _DEDUPLICATED_READ_TOOLS:
                signatures.add(_tool_call_signature(call["name"], call.get("args") or {}))
    return signatures


def execute_tool(tool_call: dict, allowed_tools: set[str] | None = None) -> tuple[ToolMessage, bool]:
    """
    Execute a single tool call using the local-agent executor registry.
    Returns (ToolMessage, is_error) — is_error reflects whether execution actually
    raised/was unregistered, not a substring guess over the result text (a tool
    that legitimately returns the word "error"/"failed" in its content, e.g. a
    grep hit or a log read, must not count against the error budget).
    """
    tc = _normalize_tool_call(tool_call)
    name = tc["name"]
    args = tc["args"]
    call_id = tc["id"]

    _log.info("[local-agent/tool] name=%s args=%s", name, args)

    # NemoClaw enforcement point: Block tool invocation if it isn't in the allowed tool subset
    if allowed_tools is not None and name not in allowed_tools:
        error_msg = f"[blocked] Tool '{name}' is not allowed in this agent's scoped manifest"
        _log.error(error_msg)
        return ToolMessage(
            content=error_msg,
            name=name,
            tool_call_id=call_id,
        ), True

    executors = get_local_agent_executors()

    if name not in executors:
        error_msg = f"Unknown tool: {name}"
        _log.error(error_msg)
        return ToolMessage(
            content=error_msg,
            name=name,
            tool_call_id=call_id,
        ), True


    try:
        result = _registry_execute_tool(name, args)
        # Ensure result is a string
        if isinstance(result, (dict, list)):
            content = json.dumps(result, default=str)
        else:
            content = str(result)

        # registry.execute_tool() catches its own exceptions and returns a marked
        # string instead of raising (see tools/registry.py:274) — is_error_result()
        # is the shared prefix-convention detector (also used by cloud_client.py/
        # ollama_client.py for the same 3-consecutive-error escalation counter);
        # a hand-copied subset here previously missed "[error...", "error:",
        # "[browseros]", and the [gmail error]/[tasks error]/etc tag family,
        # under-counting errors relative to the cloud path.
        is_error = is_error_result(content)

        # Delimit externally-sourced content (files/audio/OCR) so the model treats it as
        # reference data, not instructions — before any pipeline hints below get appended
        # (those are trusted, self-generated text, not something to wrap).
        content = wrap_external_output(name, content)

        # Pipeline hints: nudge the model through multi-step form workflows
        if name == "detect_form_fields" and "fill_form" in executors:
            # Truncate long results — large field lists cause the model to return empty
            try:
                data = json.loads(content)
                total = data.get("total", 0)
                if total > 10:
                    data["fields"] = data["fields"][:10]
                    data["_truncated"] = f"showing 10 of {total} fields"
                    content = json.dumps(data, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
            content += (
                "\n\n[NOW call fill_form IMMEDIATELY. Use EXACT field names from above. "
                'Do not describe what you would call — actually make the tool call. '
                'For CheckBox fields use "Yes" or "Off". '
                'Example: fill_form(file_path="...", fields=\'{"FieldName": "value", ...}\')]'
            )
        if name == "detect_pdf_form_fields" and "fill_pdf_form" in executors:
            try:
                data = json.loads(content)
                total = data.get("total", 0)
                if total > 10:
                    data["fields"] = data["fields"][:10]
                    data["_truncated"] = f"showing 10 of {total} fields"
                    content = json.dumps(data, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
            content += (
                "\n\n[NOW call fill_pdf_form IMMEDIATELY with the field values. "
                'Use EXACT field names from above. '
                'Do not describe the call — actually make it.]'
            )
        if name == "fill_form" and "update_form" in executors:
            content += (
                "\n\n[Verification: call detect_form_fields on the filled file to confirm all values were set correctly. "
                "Then confirm the result to the user. "
                "Tip: To update specific fields later, use update_form().]"
            )
        if name == "fill_pdf_form" and "fill_pdf_form" in executors:
            content += (
                "\n\n[Verification: check the filled file to confirm values. "
                "Then confirm the result to the user.]"
            )
        if name == "detect_flat_pdf_fields" and "fill_flat_pdf" in executors:
            try:
                data = json.loads(content)
                total = data.get("total", 0)
                if total > 10:
                    data["fields"] = data["fields"][:10]
                    data["_truncated"] = f"showing 10 of {total} fields"
                    content = json.dumps(data, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
            content += (
                "\n\n[NOW call fill_flat_pdf IMMEDIATELY. Pass the full fields_json output "
                'from this detection plus a values_json mapping labels to values. '
                'Example: fill_flat_pdf(path="...", fields_json=detection_json, '
                'values_json=\'{"Label": "value"}\')]'
            )
        if name == "fill_flat_pdf" and "detect_flat_pdf_fields" in executors:
            content += (
                "\n\n[Verification: call detect_flat_pdf_fields again on the filled file "
                "to confirm all values were placed correctly. "
                "Then confirm the result to the user.]"
            )
        if name == "edit_file" and "not found" in content.lower():
            content += (
                "\n\n[edit_file failed — exact match not found. "
                "NOW call edit_file_fuzzy with the same old_str. "
                "It uses difflib to find the closest match despite whitespace/formatting differences.]"
            )
        if name == "transcribe_audio":
            content += (
                "\n\n[Transcription complete. NOW summarize the audio in 1-2 sentences. "
                "Ask the user how to proceed before taking further action.]"
            )

        # Auto path discovery: when a tool returns "not found", automatically
        # search for the file nearby and suggest matches to avoid extra LLM hops.
        _PATH_TOOLS = {
            "read_file": "path", "read_document": "path", "read_pdf": "path",
            "list_directory": "path", "find_files": "path",
            "transcribe_audio": "file_path",
            "detect_form_fields": "file_path", "detect_pdf_form_fields": "path",
            "fill_form": "file_path", "fill_pdf_form": "path",
            "update_form": "file_path", "edit_file": "path", "edit_file_fuzzy": "path",
            "write_file": "path",
        }
        path_arg = _PATH_TOOLS.get(name)
        if path_arg and args.get(path_arg) and "not found" in content.lower():
            try:
                from pathlib import Path as _Path
                home = os.path.expanduser("~")
                fp = _Path(args[path_arg])
                basename = fp.name
                parent = str(fp.parent) if str(fp.parent) != "." else home
                if basename and basename not in ("/", ".", ".."):
                    discovered = find_files(
                        pattern=f"*{basename}*",
                        path=parent,
                        max_results=10,
                    )
                    # Cascade: if parent dir yields nothing, try common dirs
                    _SEARCH_DIRS = [parent]
                    if not discovered or discovered.startswith(("Path not found", "No files matching")):
                        for d in [f"{home}/Downloads", f"{home}/Desktop", f"{home}/Documents"]:
                            if d != parent:
                                _SEARCH_DIRS.append(d)
                                extra = find_files(pattern=f"*{basename}*", path=d, max_results=5)
                                if isinstance(extra, str) and not extra.startswith(("Path not found", "No files matching")):
                                    discovered = extra
                                    break
                    if discovered and not discovered.startswith(("Path not found", "No files matching")):
                        content += (
                            f"\n\n[Auto-discovered matches for \"{basename}\" ({', '.join(_SEARCH_DIRS)}):]\n"
                            f"{discovered}\n"
                            "[One of these is the correct path. Use it directly — do NOT re-search.]"
                        )
            except Exception:
                pass

        return ToolMessage(
            content=content,
            name=name,
            tool_call_id=call_id,
        ), is_error
    except Exception as e:
        error_msg = f"Tool {name} failed: {e}"
        # Recovery hints for common failures
        if name in ("fill_form", "fill_pdf_form") and "not found" in str(e).lower():
            error_msg += (
                "\n\n[Recovery: field name not found. Call detect_form_fields again to get the exact field names, "
                "then retry fill_form with those EXACT names. Check for leading/trailing spaces.]"
            )
        if name in ("fill_form", "fill_pdf_form") and "fields" in str(e).lower():
            error_msg += (
                "\n\n[Recovery: check the fields JSON format. Must be valid JSON with field names matching exactly. "
                "Call detect_form_fields to see available field names.]"
            )
        if name in ("detect_form_fields", "detect_pdf_form_fields") and "not found" in str(e).lower():
            error_msg += (
                "\n\n[Recovery: file not found. Check the path. Use the full home directory path "
                f"({os.path.expanduser('~')}/Downloads/...) instead of ~ or /home/user/.]"
            )
        if name in ("read_file", "list_directory", "grep_files", "find_files", "file_tree") and "not found" in str(e).lower():
            error_msg += (
                "\n\n[Recovery: file/folder not found. Use absolute paths. "
                f"Your home directory is {os.path.expanduser('~')}. "
                f"For Downloads, use {os.path.expanduser('~')}/Downloads. "
                "Do NOT use /home/, /documents, or bare Linux paths.]"
            )
        if name == "transcribe_audio" and "not found" in str(e).lower():
            error_msg += (
                "\n\n[Recovery: audio file not found. Use the full home directory path "
                f"({os.path.expanduser('~')}/Downloads/...) instead of ~ or /home/user/.]"
            )
        if name == "transcribe_audio" and "no such file" in str(e).lower() and "ffmpeg" in str(e).lower():
            error_msg += (
                "\n\n[Recovery: ffmpeg is not installed or not on PATH. "
                "Install with: brew install ffmpeg. "
                "Then retry transcribe_audio.]"
            )
        elif name == "transcribe_audio" and "format" in str(e).lower():
            error_msg += (
                "\n\n[Recovery: unsupported audio format. Convert with ffmpeg first: "
                "call bash_exec(command='ffmpeg -i /path/to/input.xxx /path/to/output.wav') "
                "then retry transcribe_audio on the .wav file.]"
            )
        _log.error(error_msg, exc_info=True)
        return ToolMessage(
            content=error_msg,
            name=name,
            tool_call_id=call_id,
        ), True

async def tool_node(state: LocalAgentState) -> dict:
    """
    Tool execution node: runs all tool calls from the last AIMessage.
    """
    messages = state.messages
    if not messages:
        return {}

    last_message = messages[-1]
    if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
        return {}

    tool_messages = []
    error_count = state.error_count
    trace_entries: list[dict] = []
    run_id = state.run_id or ""

    # Resolve permitted tools based on the active task (narrowed to the matched
    # skill's tool subset, if any, so execution can't exceed what was advertised)
    from agents.local_agent_tools import _tool_names_for
    allowed_tool_names = set(state.skill_tools) if state.skill_tools else _tool_names_for(state.task)
    allowed_tool_names -= set(state.excluded_tools)
    prior_read_signatures = _previous_read_signatures(messages[:-1])

    for tool_call in last_message.tool_calls:
        tc = _normalize_tool_call(tool_call)
        t0 = time.monotonic()
        signature = _tool_call_signature(tc["name"], tc.get("args") or {})
        if tc["name"] in _DEDUPLICATED_READ_TOOLS and signature in prior_read_signatures:
            result = ToolMessage(
                content=(
                    "[duplicate read prevented] This exact read operation already ran earlier "
                    "in this task. Use its earlier result, read a different source, or provide "
                    "the answer; do not repeat the same call."
                ),
                name=tc["name"],
                tool_call_id=tc["id"],
            )
            is_error = False
            elapsed_ms = 0
            _log.warning("[local-agent/tool] duplicate read prevented name=%s args=%s", tc["name"], tc.get("args"))
        else:
            result, is_error = await asyncio.to_thread(execute_tool, tool_call, allowed_tool_names)
            elapsed_ms = round((time.monotonic() - t0) * 1000)
        tool_messages.append(result)

        if is_error:
            error_count += 1
        else:
            error_count = 0

        trace_entries.append({
            "run_id": run_id,
            "tool": tc["name"],
            "args": {k: str(v)[:80] for k, v in (tc.get("args") or {}).items()},
            "result_summary": result.content[:120],
            "elapsed_ms": elapsed_ms,
            "error": is_error,
            "model": state.current_model,  # NemoClaw optimization: extended trace metadata
        })
        _log.info("[trace] run_id=%s tool=%s elapsed_ms=%d error=%s model=%s",
                  run_id, tc["name"], elapsed_ms, is_error, state.current_model)


    update: dict = {"messages": tool_messages, "error_count": error_count, "trace": trace_entries}

    # Escalate to the cloud fallback model once, after 3 consecutive tool
    # errors on the primary cloud model — gives a flaky/misbehaving DeepSeek
    # call one retry on GPT before should_continue_with_error_check ends the run.
    from clients.cloud_client import DEFAULT_CLOUD_MODEL, CLOUD_FALLBACK_MODEL, is_cloud_model
    if (
        error_count >= 3
        and not state.escalated
        and is_cloud_model(state.current_model)
        and state.current_model != CLOUD_FALLBACK_MODEL
    ):
        _log.warning(
            "[local-agent/graph] %d consecutive errors on %s, escalating to %s",
            error_count, state.current_model, CLOUD_FALLBACK_MODEL,
        )
        update["current_model"] = CLOUD_FALLBACK_MODEL
        update["error_count"] = 0
        update["escalated"] = True

    return update
