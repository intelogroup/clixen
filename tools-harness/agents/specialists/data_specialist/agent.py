"""Data-analysis specialist: ReAct loop entry point (public API)."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import ollama

from tools.registry import execute_tool

from ._helpers import DataResult, _STRUCTURED_SUFFIXES, _data_tool_schemas, _build_system_prompt, _analyze_dataset
# Import for side effect: registers built-in data tool schemas/executors.
from . import _advanced as _advanced  # noqa: F401
from . import _schemas as _schemas  # noqa: F401


def run_data_specialist(
    query: str,
    model: str = "gemma4:12b-mlx",
    known_path: str | None = None,
    max_steps: int = 10,
    timeout_s: float = 600.0,
) -> DataResult:
    """
    Analyze a data file — statistics, charts, SQL queries.

    Fast path: if known_path is a structured file, run analyze_dataset directly.
    Otherwise, uses a ReAct loop with data tools.
    """
    t0 = time.time()

    # ── Guard: no file path known → ask for it ──
    if not known_path and not re.search(r"[\"']?/[\w./-]+\.(?:csv|tsv|json|parquet|xlsx)\b", query):
        _data_analysis_pattern = re.compile(
            r"\b(?:analy[sz]e|stats?|statistics?|plot|chart|graph|correlation|regression|"
            r"compare|summarize|summary|table|run |describe|clean|explore|t.test|anova|pca|cluster|"
            r"correlate|forecast|arima|survival)\b",
            re.I,
        )
        if _data_analysis_pattern.search(query):
            return DataResult(
                text=(
                    "I'd be happy to analyze that data! But I need to know which file to work with. "
                    "Please provide the path to your data file (CSV, JSON, Parquet, Excel, etc.)"
                ),
                elapsed_s=time.time() - t0,
            )

    # ── Fast path: known path → analyze_dataset immediately ──
    # Only for simple EDA queries, not comprehensive analysis
    _intent_is_comprehensive = bool(re.search(
        r"\b(full\s*analysis|comprehensive|compare|all.*test|complete\s*analysis|"
        r"run.*all|deep\s*analy|thorough|full\s*report|everything)\b",
        query, re.I
    ))
    if known_path and not _intent_is_comprehensive:
        if not os.path.exists(known_path):
            return DataResult(
                paths=[known_path],
                elapsed_s=time.time() - t0,
                error=f"File not found: {known_path}",
            )
        suffix = Path(known_path).suffix.lower()
        if suffix in _STRUCTURED_SUFFIXES:
            _log.info("[data] fast path: analyze %s", known_path)
            text = _analyze_dataset(known_path)
            return DataResult(
                text=text,
                paths=[known_path],
                tool_trace=["analyze_dataset"],
                elapsed_s=time.time() - t0,
            )

    # ── Try to extract path from query (simple EDA only) ──
    if not _intent_is_comprehensive:
        abs_path_match = re.search(r"/(?:[^\"'\s]+\/)*[^\"'\s]+", query)
        if abs_path_match:
            candidate = abs_path_match.group(0)
            if os.path.isfile(candidate) and Path(candidate).suffix.lower() in _STRUCTURED_SUFFIXES:
                text = _analyze_dataset(candidate)
                return DataResult(
                    text=text,
                    paths=[candidate],
                    tool_trace=["analyze_dataset"],
                    elapsed_s=time.time() - t0,
                )

    # ── ReAct loop ──
    tools = _data_tool_schemas()
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

    # Inject known path into messages when fast path was skipped
    if known_path and os.path.isfile(known_path):
        messages.append({
            "role": "system",
            "content": f"The data file is at: {known_path}\n"
                       f"Start your analysis by calling analyze_dataset(path=\"{known_path}\") to understand the data."
        })

    tool_trace: list[str] = []
    last_text: str = ""
    chart_path: str = ""

    for step in range(max_steps):
        if time.time() - t0 > timeout_s:
            return DataResult(
                text=last_text,
                chart_path=chart_path,
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
            return DataResult(
                tool_trace=tool_trace,
                elapsed_s=time.time() - t0,
                error=f"ollama error: {e}",
            )

        msg = resp.get("message", {})
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            if content.strip():
                return DataResult(
                    text=content,
                    chart_path=chart_path,
                    tool_trace=tool_trace,
                    elapsed_s=time.time() - t0,
                )
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
            _log.info("[data] step %d: %s(%s)", step, name, json.dumps(args, default=str)[:120])

            try:
                result = execute_tool(name, args)
                result_str = str(result) if result else ""
            except Exception as e:
                result_str = f"Error: {type(e).__name__}: {e}"

            # Track chart paths (URL-based format)
            if not chart_path:
                m = re.search(r"/static/charts/\S+\.(?:png|svg|pdf)", result_str)
                if m:
                    chart_path = m.group(0).strip()

            if name == "analyze_dataset":
                last_text = result_str

            messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
            messages.append({"role": "tool", "content": result_str})

    return DataResult(
        text=last_text,
        chart_path=chart_path,
        tool_trace=tool_trace,
        elapsed_s=time.time() - t0,
        error="max steps reached" if len(tool_trace) >= max_steps else None,
    )
