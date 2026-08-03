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

import ollama
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agents.local_agent_state import LocalAgentState
from agents.local_agent_tools import get_local_agent_tools, get_local_agent_executors
from tools.registry import execute_tool as _registry_execute_tool, is_error_result
from tools.filesystem import find_files
from tools.injection_guard import wrap_external_output
from clients.cloud_client import is_cloud_model
from clients.ollama_client import DEFAULT_MODEL as _LOCAL_DEFAULT_MODEL
from log_config import setup_logging as _setup_logging

_log = _setup_logging("local_agent_nodes")

# ponytail: cooldown cache — after cloud call fails, skip retry for N seconds.
# Prevents 50 consecutive 404s in one agent run (observed: 15:07-15:13, Jul 8).
_cloud_deadline: dict[str, float] = {}
_cloud_lock = threading.Lock()
_CLOUD_COOLDOWN = 300

# System prompt for local-agent (reused from harness.py)
def _build_system_prompt(task: str, tool_schemas: list[dict], home: str) -> str:
    """Build a system prompt dynamically from tool schemas + task type.

    Tool descriptions are extracted live from the actual schemas — no
    hardcoded tool lists. The prompt structure adjusts per task:
    - code: full coding workflow, no form/audio bloat
    - document: doc-creation focused, no code/form tools
    - full: everything
    """
    import os

    name_map = {t["function"]["name"]: t["function"]["description"].split(".")[0] for t in tool_schemas}
    has = lambda n: n in name_map

    lines = []

    # Role
    if task == "code":
        lines.append("You are local-agent, a coding assistant on this computer. "
                      "You write, edit, search, and restructure code files.")
    elif task == "document":
        lines.append("You are local-agent, a document creation assistant. "
                      "You generate PDFs, Excel files, and PowerPoints from content.")
    else:
        lines.append("You are local-agent, a file and folder assistant on this computer.")

    lines.append("")

    # Critical instruction
    lines.append("CRITICAL: Call a tool for every request. Never describe what you'd do. "
                 "Never write tool syntax as plain text — make actual tool calls.")
    lines.append("")

    # Tool list — generated from schemas
    lines.append("TOOLS:")

    # Read tools
    read_group = []
    for n in ["read_file", "read_many_files", "read_document", "read_pdf", "list_directory", "find_files"]:
        if has(n):
            read_group.append(f"  {n} — {name_map[n]}")
    if has("grep_files"):
        read_group.append("  grep_files — search file contents by regex")
    if has("file_tree"):
        read_group.append("  file_tree — recursive project layout")
    if has("file_info"):
        read_group.append("  file_info — size, type, modified date")
    if has("parse_code"):
        read_group.append("  parse_code — extract functions, classes, imports from code")
    if read_group:
        lines.append("READ:")
        lines.extend(read_group)
        lines.append("")

    # Write tools
    write_group = []
    for n in ["write_file", "edit_file", "edit_file_fuzzy", "append_file", "rename_file", "copy_file", "create_directory"]:
        if has(n):
            write_group.append(f"  {n} — {name_map[n]}")
    if write_group:
        lines.append("WRITE:")
        lines.extend(write_group)
        lines.append("")

    # Form tools
    form_group = []
    for n in ["detect_form_fields", "detect_pdf_form_fields", "fill_form", "fill_pdf_form",
              "update_form", "detect_flat_pdf_fields", "fill_flat_pdf",
              "vision_detect_form_fields", "vision_fill_form_fields"]:
        if has(n):
            form_group.append(f"  {n} — {name_map[n]}")
    if form_group and task != "code":
        lines.append("FORM (PDF/DOCX):")
        lines.extend(form_group)
        lines.append("")

    # Doc creation tools
    doc_group = []
    for n in ["create_pdf", "markdown_to_pdf", "data_to_xlsx", "markdown_to_pptx", "json_to_docx", "markdown_to_docx"]:
        if has(n):
            doc_group.append(f"  {n} — {name_map[n]}")
    if doc_group and task != "code":
        lines.append("DOCUMENT CREATION:")
        lines.extend(doc_group)
        lines.append("")

    # Audio tool
    if has("transcribe_audio") and task != "code":
        lines.append("AUDIO:")
        lines.append(f"  transcribe_audio — {name_map['transcribe_audio']}")
        lines.append("")

    # Shell tool
    if has("bash_exec"):
        lines.append("SHELL:")
        lines.append(f"  bash_exec — {name_map['bash_exec']}")
        lines.append("")

    # Task-specific workflow
    if task == "code":
        lines.append("CODING WORKFLOW:")
        lines.append("  1. Use file_tree to understand project structure before making changes.")
        lines.append("  2. Use grep_files to find relevant code.")
        lines.append("  3. Use read_file or read_many_files to see code before editing.")
        lines.append("  4. Use edit_file for exact-match edits. If it returns 'not found', "
                      "use edit_file_fuzzy which handles whitespace differences.")
        lines.append("  5. After editing, run the code/test via bash_exec or run_python to verify.")
        lines.append("  6. If test/run fails, read the error output, fix via edit_file, re-run.")
        lines.append("")
        lines.append("SUBAGENTS:")
        lines.append("- ask_dev_agent: git ops (commit/push/merge/rebase), REPL inspection, "
                      "window/system process queries.")
        lines.append("- ask_research_agent: library docs, API refs, Wikipedia, arXiv, PubMed, "
                      "sports scores — use when you need external knowledge.")
        lines.append("- ask_web_search: search internet for solutions, Stack Overflow, "
                      "news, docs — use for recent info or troubleshooting.")
        lines.append("")
        lines.append("SAFETY:")
        lines.append("- Confirm with the user before deleting files.")
        lines.append("- Never read or edit files inside .git/ directories.")
        lines.append("")
        lines.append("ENGINEERING DISCIPLINE (stolen from superpowers/gsd/gstack):")
        lines.append("- VERIFY BEFORE DONE: never report success without running it. After code "
                      "changes, execute tests/build/lint via bash_exec or run_python and confirm green. "
                      "Claims like 'this should work' are failures, not completions.")
        lines.append("- REPRODUCE BEFORE FIX: for a bug, first reproduce it (run → fail), then fix, "
                      "then confirm the failure is gone. Do not guess-fix.")
        lines.append("- TDD WHEN TESTS EXIST: if the repo has a test suite, add/run a test that fails "
                      "first, then implement the smallest change that passes.")
        lines.append("- ATOMIC COMMITS: commit via ask_dev_agent with a message describing WHAT changed. "
                      "One logical change per commit; do not bundle unrelated edits.")
        lines.append("- SEPARATE BUILD FROM VERIFY: finish the edit, then verify in a distinct step. "
                      "Never blend 'I'll fix it and it'll work' — prove it instead.")
        lines.append("")
    elif task in ("full",):
        lines.append("FORM WORKFLOW (PDF/DOCX):")
        lines.append("  1. Call detect_form_fields to find all fields.")
        lines.append("  2. Review field names/types from detection output.")
        lines.append("  3. Call fill_form with field values — use {{\"FieldName\": \"value\"}} format.")
        lines.append("     For CheckBox use \"Yes\" or \"Off\". For signatures, use a PNG path.")
        lines.append("  4. Verify by calling detect_form_fields on the filled file.")
        lines.append("")

    # Search rule
    if has("find_files"):
        lines.append("FILE SEARCH: Use find_files with a glob pattern for specific file types "
                      f"(e.g. find_files(pattern='*.m4a', path='{home}/Downloads')). "
                      "list_directory is capped and may miss files.")
        lines.append("")

    # Path rules
    lines.append(f"PATH RULES: Use absolute paths. For files in Downloads/Desktop/Documents, "
                  f"use {home}/Downloads/file.pdf. "
                  "Do NOT use ~ or /home/user/ — tools reject those paths.")
    lines.append("")

    lines.append("Call the tool immediately for every request. "
                 "After writes/edits, confirm result in one sentence. "
                 "For reads, return content directly with no wrapper text.")
    lines.append("")
    lines.append("HONESTY: If a tool result doesn't contain the answer, a file/field can't be found, "
                 "or you're not sure, say so plainly instead of guessing or inventing plausible-looking "
                 "content, paths, or field values. State uncertainty when it exists.")

    return "\n".join(lines)


def _get_system_prompt(model: str, task: str = "full", query: str = "", tools: list[dict] | None = None) -> str:
    """Build system prompt with memory recall prepended."""
    import os
    home = os.path.expanduser("~")
    base = _build_system_prompt(task, tools or [], home)
    if query:
        from tools.memory_tools import recall_block
        mem_block = recall_block(query)
        if mem_block:
            return mem_block + "\n" + base
    return base


_CLIENT = None


def _get_client() -> ollama.Client:
    # Bounded timeout so a stalled gemma4 call (long prompts hang — see CLAUDE.md)
    # raises instead of blocking the whole graph forever. 120s covers a slow cold
    # gemma4:12b-mlx round; a real hang trips it and the graph's consecutive-error
    # handler ends the run cleanly instead of the bot going silent.
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    host = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL")
    _CLIENT = ollama.Client(host=host, timeout=120) if host else ollama.Client(timeout=120)
    return _CLIENT


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
                return _ollama_chat(_LOCAL_DEFAULT_MODEL, messages, tools, temperature=temperature)

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
            return _ollama_chat(_LOCAL_DEFAULT_MODEL, messages, tools, temperature=temperature)
    return _ollama_chat(model, messages, tools, temperature=temperature)


def _ollama_chat(model: str, messages: list[dict], tools: list[dict], temperature: float = 0.0):
    """
    Call ollama.chat with tools.
    Uses native ollama client (same as ollama_client.py).
    Returns the response object (ChatResponse).
    """
    # Build options
    options = {"temperature": temperature}

    # Model-specific options
    if model.startswith("gemma4"):
        options["num_ctx"] = 16384  # gemma4 needs large context for tool results + form data
        options["think"] = False  # thinking mode burns the num_predict budget
    if "qwen3" in model:
        options["think"] = False
        options["temperature"] = 0.7
        options["top_p"] = 0.8
        options["top_k"] = 20
        options["repeat_penalty"] = 1.05

    from ollama._types import ResponseError
    try:
        response = _get_client().chat(
            model=model,
            messages=messages,
            tools=tools,
            options=options,
        )
        return response  # Returns ChatResponse object
    except ResponseError as e:
        if "not found" in str(e).lower():
            msg = f"Model '{model}' not found in Ollama. Run: ollama pull {model}"
            _log.error("[local-agent] %s", msg)
            raise RuntimeError(msg) from e
        raise


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
    tools = get_local_agent_tools(state.task)

    # Add system prompt if not present
    if not has_system:
        _query_text = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")
        system_prompt = _get_system_prompt(model, task=state.task, query=_query_text, tools=tools)
        ollama_messages.insert(0, {"role": "system", "content": system_prompt})
    _log.info("[local-agent/graph] SENDING TO OLLAMA: %d tools, first tool: %s", len(tools), tools[0]['function']['name'] if tools else 'NONE')

    # Call the model (dispatches cloud vs. local) — run sync client in a thread
    # so the node-level timeout in local_agent_graph.py can interrupt a hang.
    response = await asyncio.to_thread(
        _chat,
        model=model,
        messages=ollama_messages,
        tools=tools,
        temperature=0.0 if model.startswith("gemma4") else 0.7,
    )

    _log.debug("[local-agent/graph] ollama response received")
    _log.info("[local-agent/graph] MODEL RESPONSE: content='%s', has_tool_calls=%s", 
              (response.message.content or '')[:100], bool(response.message.tool_calls))

    # Convert ollama response back to LangChain AIMessage
    response_msg = response.message  # ChatResponse.message is a Message object
    msg_content = getattr(response_msg, "content", "") or ""
    tool_calls = getattr(response_msg, "tool_calls", None)

    _log.debug("[local-agent/graph] msg_content len=%d has_tool_calls=%s", len(msg_content), bool(tool_calls))

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
        return {}

    if not plan_text:
        return {}

    _log.info("[local-agent/graph] plan=%s", plan_text[:200])
    return {"messages": [SystemMessage(content=f"[Plan]\n{plan_text}")]}


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

    query = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    transcript = "\n".join(
        f"[tool result] {m.content[:2000]}" for m in messages if isinstance(m, ToolMessage)
    )[-12000:]

    prompt = [
        {"role": "system", "content": "You check whether an assistant's final answer actually "
                                       "addresses the user's request and is consistent with the "
                                       "tool results shown. Reply with exactly 'OK' if it's fine, "
                                       "or one short line explaining what's wrong if it isn't."},
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

    # Resolve permitted tools based on the active task
    from agents.local_agent_tools import _tool_names_for
    allowed_tool_names = _tool_names_for(state.task)

    for tool_call in last_message.tool_calls:
        tc = _normalize_tool_call(tool_call)
        t0 = time.monotonic()
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
