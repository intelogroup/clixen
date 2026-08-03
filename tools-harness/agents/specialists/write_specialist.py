"""
Write specialist subagent.

Mission: create, edit, and manage files — write new files, edit existing ones,
create directories, generate documents from templates.

Tools: write_file, edit_file, append_file, create_directory, copy_file, rename_file,
       data_to_xlsx, json_to_docx, markdown_to_pdf, markdown_to_pptx, markdown_to_docx

Public API:
    run_write_specialist(query, model="gemma4", known_path=None, max_steps=6) -> WriteResult
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import ollama
from pydantic import BaseModel, Field

from tools.registry import ALL_TOOLS, execute_tool, tools_with_tags

_log = logging.getLogger("write_specialist")

WRITE_TOOL_NAMES = tools_with_tags("spec_write")


def _write_tool_schemas() -> list[dict]:
    names = {t["function"]["name"] for t in ALL_TOOLS}
    available = WRITE_TOOL_NAMES & names
    return [t for t in ALL_TOOLS if t["function"]["name"] in available]


class WriteResult(BaseModel):
    paths: list[str] = Field(default_factory=list)
    tool_trace: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    error: str | None = None


_SYSTEM_PROMPT = """You are WRITE. Your job: create, edit, and manage files and documents.

TOOLS:
  write_file(path, content)         — create a new file with content
  edit_file(path, old_str, new_str) — find and replace text in a file
  append_file(path, content)        — append text to an existing file
  create_directory(path)            — create a new directory (nested ok)
  copy_file(src, dest)              — copy a file
  rename_file(src, dest)            — rename/move a file
  data_to_xlsx(data_path, output_path)   — convert CSV/JSON to Excel
  json_to_docx(json_path, output_path)   — convert JSON to DOCX
  markdown_to_pdf(md_path, output_path)  — convert Markdown to PDF
  markdown_to_pptx(md_path, output_path) — convert Markdown to PowerPoint
  markdown_to_docx(md_path, output_path) — convert Markdown to DOCX
  text_to_file(path, content)            — write plain text to file

RULES:
- For NEW files: use write_file or text_to_file.
- For EXISTING files: use edit_file (partial changes) or append_file (add to end).
- For format conversion (CSV→XLSX, MD→PDF): use the conversion tools.
- Create parent directories first (create_directory) if needed.
- Return the path(s) of created/modified files.
- Stop after {max_steps} calls."""


def _build_system_prompt(max_steps: int) -> str:
    return _SYSTEM_PROMPT.format(max_steps=max_steps)


def run_write_specialist(
    query: str,
    model: str = "gemma4:12b-mlx",
    known_path: str | None = None,
    max_steps: int = 6,
    timeout_s: float = 300.0,
) -> WriteResult:
    t0 = time.time()

    tools = _write_tool_schemas()
    tool_names = {t["function"]["name"] for t in tools}
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
    created_paths: list[str] = []

    for step in range(max_steps):
        if time.time() - t0 > timeout_s:
            return WriteResult(
                paths=created_paths,
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
            return WriteResult(
                paths=created_paths,
                tool_trace=tool_trace,
                elapsed_s=time.time() - t0,
                error=f"ollama error: {e}",
            )

        msg = resp.get("message", {})
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            break

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

            try:
                result = execute_tool(name, args)
                result_str = str(result) if result else ""
            except Exception as e:
                result_str = f"Error: {type(e).__name__}: {e}"

            # Track created/modified paths. A rename/move's "src" no longer exists there
            # once it succeeds — drop it so a prior write_file's path isn't reported as a
            # second, still-existing file after it's actually been relocated.
            if name == "rename_file":
                src = args.get("src", "")
                if src in created_paths:
                    created_paths.remove(src)
            for key in ("path", "dest", "output_path"):
                p = args.get(key, "")
                if p and p not in created_paths:
                    created_paths.append(p)

            messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
            messages.append({"role": "tool", "content": result_str})

    return WriteResult(
        paths=created_paths,
        tool_trace=tool_trace,
        elapsed_s=time.time() - t0,
    )
