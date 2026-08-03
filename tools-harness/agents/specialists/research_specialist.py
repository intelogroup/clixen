"""
Research specialist subagent.

Mission: search academic and scientific literature across sources.

Tools: search_pubmed, search_arxiv, semantic_file_search, paper_qa

Public API:
    run_research_specialist(query, model="gemma4", max_steps=6) -> ResearchResult
"""

from __future__ import annotations

import json
import logging
import re
import time

from clients.ollama_client import DEFAULT_MODEL
import ollama
from pydantic import BaseModel, Field

from tools.registry import ALL_TOOLS, execute_tool, tools_with_tags

_log = logging.getLogger("research_specialist")

RESEARCH_TOOL_NAMES = tools_with_tags("spec_research")


def _research_tool_schemas() -> list[dict]:
    return [t for t in ALL_TOOLS if t["function"]["name"] in RESEARCH_TOOL_NAMES]


class ResearchResult(BaseModel):
    text: str = ""
    sources: list[str] = Field(default_factory=list)
    tool_trace: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    error: str | None = None


_SYSTEM_PROMPT = """You are RESEARCH ASSISTANT. Your job: search academic literature for scientific answers.

TOOLS:
  search_pubmed(query, max_results)   — search PubMed for biomedical papers
  search_arxiv(query, max_results)    — search arXiv for preprints
  semantic_file_search(query, top_k)  — search local indexed papers semantically

RULES:
- For biomedical/health questions: use search_pubmed.
- For CS/physics/math: use search_arxiv.
- For local paper collections: use semantic_file_search.
- Summarize key findings from the results.
- Do NOT fabricate citations — only report what the search returned.
- Stop after {max_steps} calls."""


def _build_system_prompt(max_steps: int) -> str:
    return _SYSTEM_PROMPT.format(max_steps=max_steps)


def run_research_specialist(
    query: str,
    model: str = DEFAULT_MODEL,
    max_steps: int = 6,
    timeout_s: float = 300.0,
) -> ResearchResult:
    t0 = time.time()

    tools = _research_tool_schemas()
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
    text: str = ""

    for step in range(max_steps):
        if time.time() - t0 > timeout_s:
            return ResearchResult(
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
            return ResearchResult(
                tool_trace=tool_trace,
                elapsed_s=time.time() - t0,
                error=f"ollama error: {e}",
            )

        msg = resp.get("message", {})
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            text = content
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

            if not text:
                text = result_str

            messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
            messages.append({"role": "tool", "content": result_str})

    return ResearchResult(
        text=text,
        tool_trace=tool_trace,
        elapsed_s=time.time() - t0,
    )
