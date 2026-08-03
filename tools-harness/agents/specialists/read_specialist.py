"""
Read specialist subagent.

Mission: read a file's contents using the right tool for its format.
Auto-detects tool by file extension (fast path — no LLM needed).
Falls back to ReAct loop for ambiguous queries ("read the malaria PDF in Downloads").

Public API:
    run_read_specialist(query, model="gemma4", known_path=None, max_steps=5) -> ReadResult
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from clients.ollama_client import DEFAULT_MODEL
import ollama
from pydantic import BaseModel, Field

from tools.registry import ALL_TOOLS, execute_tool, tools_with_tags

_log = logging.getLogger("read_specialist")

# ---------------------------------------------------------------------------
# Toolset
# ---------------------------------------------------------------------------

READ_TOOL_NAMES = tools_with_tags("spec_read")

_EXT_TO_TOOL = {
    ".txt": "read_file",
    ".md": "read_file",
    ".py": "parse_code",
    ".js": "parse_code",
    ".ts": "parse_code",
    ".go": "parse_code",
    ".rs": "parse_code",
    ".java": "parse_code",
    ".c": "parse_code",
    ".cpp": "parse_code",
    ".h": "parse_code",
    ".swift": "parse_code",
    ".sh": "read_file",
    ".yaml": "read_file",
    ".yml": "read_file",
    ".toml": "read_file",
    ".json": "parse_file",
    ".csv": "parse_file",
    ".tsv": "parse_file",
    ".xml": "parse_file",
    ".pdf": "read_pdf",
    ".docx": "read_document",
    ".doc": "read_document",
    ".pptx": "read_document",
    ".xlsx": "read_document",
    ".html": "read_file",
    ".css": "read_file",
}


def _detect_tool(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return _EXT_TO_TOOL.get(suffix)


def _read_tool_schemas() -> list[dict]:
    return [t for t in ALL_TOOLS if t["function"]["name"] in READ_TOOL_NAMES]


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

class ReadResult(BaseModel):
    text: str = ""
    path: str = ""
    tool_used: str = ""
    tool_trace: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are READ. Your only job: read the contents of a file or document and return what it contains.

User's home: {home_dir}

TOOLS:
  read_file(path)              — read a plain text or code file
  read_pdf(path, pages)        — read a PDF, optionally specify page range
  read_document(path)          — read a structured document (DOCX, PPTX, XLSX)
  parse_file(path)             — parse a structured data file (CSV, JSON, XML)
  parse_code(path)             — parse source code with structure

CHOOSE THE RIGHT TOOL:
  .txt .md .sh .yaml .html → read_file
  .py .js .ts .go .rs .java .c .swift → parse_code
  .csv .json .xml .tsv → parse_file
  .docx .doc → read_document
  .pdf → read_pdf

FILE EXTENSION IS THE PRIMARY GUIDE. A Python file (.py) MUST use parse_code, never read_pdf.

RULES:
- If the user gives you a known absolute path, use the right tool for its extension immediately.
- Do NOT call more than one read tool on the same file.
- Stop after the tool result comes back — your job is done.
- Do NOT analyze or interpret the content — just return it.
- Stop after {max_steps} calls at most."""


def _build_system_prompt(max_steps: int) -> str:
    return _SYSTEM_PROMPT.format(
        home_dir=os.path.expanduser("~"),
        max_steps=max_steps,
    )


# ---------------------------------------------------------------------------
# Fast path: auto-detect + read (no LLM)
# ---------------------------------------------------------------------------

def _fast_read(path: str, pages: str = "") -> ReadResult:
    """Direct tool execution for known paths — skips LLM entirely."""
    tool = _detect_tool(path)
    if not tool:
        return ReadResult(error=f"No tool for suffix: {Path(path).suffix}")

    t0 = time.time()
    args = {"path": path}
    if tool == "read_pdf" and pages:
        args["pages"] = pages

    try:
        result = execute_tool(tool, args)
        text = str(result) if result else ""
        return ReadResult(
            text=text,
            path=path,
            tool_used=tool,
            tool_trace=[tool],
            elapsed_s=time.time() - t0,
        )
    except Exception as e:
        return ReadResult(
            path=path,
            tool_used=tool,
            tool_trace=[tool],
            elapsed_s=time.time() - t0,
            error=f"{type(e).__name__}: {e}",
        )


# ---------------------------------------------------------------------------
# Extract content from tool results
# ---------------------------------------------------------------------------

def _extract_content(tool_name: str, result: str) -> str:
    """Extract readable content from a tool result."""
    # Most tools return the content directly as plain text
    if tool_name in {"read_file", "read_pdf", "read_document"}:
        return result
    if tool_name in {"parse_file", "parse_code"}:
        # These return structured output — pass through as-is
        return result
    return result


# ---------------------------------------------------------------------------
# ReAct loop (for ambiguous queries)
# ---------------------------------------------------------------------------

def _resolve_existing(path: str) -> str | None:
    """Return an existing absolute path for a (possibly bare-relative) path, trying the
    open project root and home dir — mirrors tools.filesystem._resolve_path fallbacks."""
    from tools.filesystem import get_project_root

    p = os.path.expanduser(path)
    if os.path.exists(p):
        return p
    if not os.path.isabs(p):
        for base in (get_project_root(), os.path.expanduser("~")):
            if base and os.path.exists(os.path.join(base, path)):
                return os.path.join(base, path)
    return None


def run_read_specialist(
    query: str,
    model: str = DEFAULT_MODEL,
    known_path: str | None = None,
    max_steps: int = 5,
    timeout_s: float = 300.0,
) -> ReadResult:
    """
    Read a file using the best tool for its format.

    Fast path: if known_path is set and the extension is recognized,
    the right tool is called directly with no LLM overhead.

    Otherwise, runs a ReAct loop to find and read the file.
    """
    t0 = time.time()

    # ── Fast path: known path + recognized extension ──
    if known_path:
        resolved = _resolve_existing(known_path)
        if not resolved:
            return ReadResult(
                path=known_path,
                elapsed_s=time.time() - t0,
                error=f"File not found: {known_path}",
            )
        if os.path.isfile(resolved):
            tool = _detect_tool(resolved)
            if tool:
                _log.info("[read] fast path: %s via %s", resolved, tool)
                return _fast_read(resolved)

    # ── Try to extract a path from the query (handles ~, absolute, and bare relative) ──
    path_match = re.search(r"(?:~|/[^\s\"'])[^\s\"']*|\b[\w./-]+\.\w{1,5}\b", query)
    if path_match:
        candidate = _resolve_existing(path_match.group(0))
        if candidate and os.path.isfile(candidate):
            tool = _detect_tool(candidate)
            if tool:
                _log.info("[read] query path: %s via %s", candidate, tool)
                return _fast_read(candidate)

    # ── ReAct loop ──
    tools = _read_tool_schemas()
    tool_names = {t["function"]["name"] for t in tools}
    home_dir = os.path.expanduser("~")
    system = _build_system_prompt(max_steps)
    from tools.memory_tools import mem_block_for
    _mem = mem_block_for(query)
    if _mem:
        system = _mem + "\n" + system

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ]

    tool_trace: list[str] = []
    last_path: str = ""
    last_tool: str = ""
    read_ok = False  # True once a read tool returns real (non-empty, non-error) content

    for step in range(max_steps):
        if time.time() - t0 > timeout_s:
            return ReadResult(
                tool_trace=tool_trace,
                elapsed_s=time.time() - t0,
                error="timeout",
            )

        try:
            resp = ollama.chat(
                model=model,
                messages=messages,
                tools=tools,
                options={"temperature": 0.1, "num_ctx": 8192},
            )
        except Exception as e:
            return ReadResult(
                tool_trace=tool_trace,
                elapsed_s=time.time() - t0,
                error=f"ollama error: {e}",
            )

        msg = resp.get("message", {})
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            # Model wants to stop. Only trust its text if a read actually succeeded —
            # otherwise it's confabulating file contents on a failed/empty read.
            if content.strip() and read_ok:
                return ReadResult(
                    text=content,
                    path=last_path,
                    tool_used=last_tool,
                    tool_trace=tool_trace,
                    elapsed_s=time.time() - t0,
                )
            if not read_ok:
                return ReadResult(
                    path=last_path,
                    tool_used=last_tool,
                    tool_trace=tool_trace,
                    elapsed_s=time.time() - t0,
                    error="no readable content (file not found or empty)",
                )
            break

        # Execute tool calls
        for tc in tool_calls:
            fn = tc.get("function", tc) if isinstance(tc, dict) else getattr(tc, "function", None)
            if not fn:
                continue
            name = fn.get("name", "") or getattr(fn, "name", "")
            args_raw = fn.get("arguments", {}) or getattr(fn, "arguments", {})
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = dict(args_raw) if args_raw else {}

            if name not in tool_names:
                continue

            tool_trace.append(name)
            _log.info("[read] step %d: %s(%s)", step, name, json.dumps(args, default=str)[:120])

            try:
                result = execute_tool(name, args)
                result_str = str(result) if result else ""
            except Exception as e:
                result_str = f"Error: {type(e).__name__}: {e}"

            if name in READ_TOOL_NAMES:
                last_path = str(args.get("path", ""))
                last_tool = name
                if result_str.strip() and not result_str.startswith("Error:"):
                    read_ok = True

            messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
            messages.append({"role": "tool", "content": result_str})

    return ReadResult(
        tool_trace=tool_trace,
        elapsed_s=time.time() - t0,
        error="max steps reached" if len(tool_trace) >= max_steps else None,
    )
