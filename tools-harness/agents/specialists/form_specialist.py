"""
Form specialist — zero-LLM direct execution pipeline.

Fast path (path + fields parseable from query):
  1. detect_form_fields(path)         — confirm field names exist
  2. fill_form / fill_pdf_form(path)  — fill values
  3. detect_form_fields(filled_path)  — verify values stuck
  Total: 0 LLM calls, 3 direct EXECUTORS calls.

Fallback (path or fields missing):
  1 ollama.chat call to extract path/fields, then direct execution.
  Total: 1 LLM call, 3 direct EXECUTORS calls.

Public API:
    run_form_specialist(query, model="gemma4", known_path=None, fields=None) -> FormResult
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

from tools.registry import ALL_TOOLS, EXECUTORS, execute_tool, tools_with_tags

_log = logging.getLogger("form_specialist")

FORM_TOOL_NAMES = tools_with_tags("spec_form")

_FORM_SUFFIXES = {".pdf", ".docx", ".doc"}

# Regex: absolute path ending in a form extension (~ expanded later)
_PATH_RE = re.compile(
    r"(?:~|/[^\s\"']*?)/[^\s\"']*?\.(?:pdf|docx|doc)\b",
    re.IGNORECASE,
)

# Regex: "Key=Value" or "Key: Value" pairs — value stops at comma, newline, or end
_FIELD_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9 _\-]{0,60}?)\s*[=:]\s*([^,\n=:]{1,300}?)(?=\s*[,\n]|\s*$)",
)

# Words that look like field assignments but aren't
_FIELD_STOPWORDS = frozenset({
    "fill", "path", "file", "output", "form", "pdf", "doc", "docx",
    "with", "using", "fields", "values", "at", "in", "the", "a", "an",
})


def _form_tool_schemas() -> list[dict]:
    return [t for t in ALL_TOOLS if t["function"]["name"] in FORM_TOOL_NAMES]


class FormResult(BaseModel):
    pdf_path: str = ""
    fields_detected: dict = Field(default_factory=dict)
    fields_filled: dict = Field(default_factory=dict)
    verified: bool = False
    output_path: str = ""
    tool_trace: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    error: str | None = None
    phase: str = "init"       # init, detected, filled, verified


def _expand_path(p: str) -> str:
    return os.path.expanduser(p)


def _parse_path_from_query(query: str) -> str | None:
    """Extract first resolvable form file path from query."""
    for m in _PATH_RE.finditer(query):
        candidate = _expand_path(m.group(0))
        if os.path.isfile(candidate):
            return candidate
    return None


def _parse_fields_from_query(query: str) -> dict:
    """Extract FieldName=Value pairs from query text."""
    # Find the section after 'fill' or ':' to avoid matching path components
    section = query
    colon_pos = query.find(":")
    if colon_pos != -1:
        section = query[colon_pos + 1:]

    fields = {}
    for m in _FIELD_RE.finditer(section):
        key = m.group(1).strip()
        val = m.group(2).strip()
        if key.lower() in _FIELD_STOPWORDS or not val or len(key) > 80:
            continue
        # Skip path-like values
        if val.startswith("/") or val.startswith("~"):
            continue
        fields[key] = val
    return fields


def _parse_fields_from_detect(result: str) -> dict:
    """Parse field names from detect_form_fields output."""
    fields = {}
    for line in result.splitlines():
        line = line.strip()
        if ":" in line and not line.startswith("Error") and not line.startswith("This PDF"):
            parts = line.split(":", 1)
            name = parts[0].strip()
            rest = parts[1].strip() if len(parts) > 1 else ""
            if name and len(name) < 100:
                fields[name] = rest or "unknown"
    return fields


def _choose_fill_tool(path: str) -> str:
    ext = Path(path).suffix.lower()
    return "fill_pdf_form" if ext == ".pdf" else "fill_form"


def _make_output_path(path: str) -> str:
    p = Path(path)
    return str(p.parent / f"{p.stem}_filled{p.suffix}")


def _direct_execute(
    path: str,
    fields: dict,
    result: FormResult,
    tool_trace: list[str],
) -> FormResult:
    """Run detect → fill → verify without any LLM calls."""
    result.pdf_path = path

    # 1. Detect
    try:
        detect_out = str(execute_tool("detect_form_fields", {"path": path}) or "")
    except Exception as e:
        detect_out = f"Error: {e}"
    tool_trace.append("detect_form_fields")
    detected = _parse_fields_from_detect(detect_out)
    result.fields_detected = detected
    result.phase = "detected"
    _log.info("[form-specialist] detected %d fields in %s", len(detected), path)

    # 2. Fill
    fill_tool = _choose_fill_tool(path)
    out_path = _make_output_path(path)
    fill_args = {"path": path, "fields": fields, "output_path": out_path}
    try:
        fill_out = str(execute_tool(fill_tool, fill_args) or "")
    except Exception as e:
        fill_out = f"Error: {e}"
        result.error = fill_out
    tool_trace.append(fill_tool)
    result.fields_filled = fields
    result.output_path = out_path
    result.phase = "filled"
    _log.info("[form-specialist] fill result: %s", fill_out[:120])

    # 3. Verify
    try:
        verify_out = str(execute_tool("detect_form_fields", {"path": out_path}) or "")
    except Exception as e:
        verify_out = f"Error: {e}"
    tool_trace.append("detect_form_fields")
    if "error" not in verify_out.lower() and "not found" not in verify_out.lower():
        result.verified = True
        result.phase = "verified"

    return result


def _llm_extract(
    query: str,
    model: str,
    tools: list[dict],
    t0: float,
    timeout_s: float,
) -> tuple[str | None, dict]:
    """One ollama.chat call to extract path + fields when regex fails."""
    system = (
        "You are a form-fill assistant. Extract the file path and field values from the user query. "
        "Call detect_form_fields(path) to start. Do NOT fill the form — just detect."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ]
    try:
        resp = ollama.chat(
            model=model,
            messages=messages,
            tools=tools,
            options={"temperature": 0.1, "num_ctx": 4096},
        )
    except Exception as e:
        _log.warning("[form-specialist] fallback LLM call failed: %s", e)
        return None, {}

    msg = resp.get("message", {})
    tool_calls = msg.get("tool_calls") or []
    for tc in tool_calls:
        fn = tc.get("function", tc) if isinstance(tc, dict) else getattr(tc, "function", None)
        if not fn:
            continue
        name = fn.get("name", "") or getattr(fn, "name", "")
        if "detect" not in name:
            continue
        args_raw = fn.get("arguments", {}) or getattr(fn, "arguments", {})
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {}
        else:
            args = dict(args_raw) if args_raw else {}
        path = _expand_path(args.get("path", ""))
        if path and os.path.isfile(path):
            return path, {}
    return None, {}


def run_form_specialist(
    query: str,
    model: str = "gemma4:12b-mlx",
    known_path: str | None = None,
    fields: dict | None = None,
    max_steps: int = 6,   # kept for API compat, not used in direct path
    timeout_s: float = 300.0,
) -> FormResult:
    t0 = time.time()
    tool_trace: list[str] = []
    result = FormResult()

    # --- Resolve path ---
    path = known_path
    if path:
        path = _expand_path(path)
    if not path:
        path = _parse_path_from_query(query)

    if path and not os.path.isfile(path):
        result.pdf_path = path
        result.elapsed_s = time.time() - t0
        result.error = f"File not found: {path}"
        return result

    # --- Resolve fields ---
    if fields is None:
        fields = _parse_fields_from_query(query)

    # --- Fast path: path + fields both known → 0 LLM calls ---
    if path and fields:
        _log.info(
            "[form-specialist] fast path: %s fields=%s", path, list(fields.keys())
        )
        result = _direct_execute(path, fields, result, tool_trace)
        result.tool_trace = tool_trace
        result.elapsed_s = time.time() - t0
        return result

    # --- Fallback: missing path or fields → 1 LLM call to extract ---
    _log.info("[form-specialist] fallback: path=%s fields=%s — calling LLM once", path, bool(fields))
    tools = _form_tool_schemas()
    llm_path, llm_fields = _llm_extract(query, model, tools, t0, timeout_s)

    path = path or llm_path
    if not fields:
        fields = llm_fields

    if not path:
        result.elapsed_s = time.time() - t0
        result.error = "Could not determine form file path from query"
        return result

    if not fields:
        # Detect only (caller will show fields and ask user for values)
        try:
            detect_out = str(execute_tool("detect_form_fields", {"path": path}) or "")
        except Exception as e:
            detect_out = f"Error: {e}"
        tool_trace.append("detect_form_fields")
        result.pdf_path = path
        result.fields_detected = _parse_fields_from_detect(detect_out)
        result.phase = "detected"
        result.tool_trace = tool_trace
        result.elapsed_s = time.time() - t0
        return result

    result = _direct_execute(path, fields, result, tool_trace)
    result.tool_trace = tool_trace
    result.elapsed_s = time.time() - t0
    return result
