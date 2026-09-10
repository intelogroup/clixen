"""Shared cloud/local chat-turn helper for specialists' hand-rolled ReAct loops.

ponytail: write/research/scraper/video/audio specialists each copy-pasted the
same ollama.chat() + dict-parsing boilerplate, hard-dependent on local Ollama
(2026-09-10 outage: unmounted external drive took the model store down, these
5 specialists had no fallback while path/read/form already did or didn't need
one). path_specialist.py proved the cloud_client.raw_completion() branch live.
Lifted here once instead of re-diverging it 5 more times.
"""
from __future__ import annotations

import json


def chat_step(ollama_client, model: str, messages: list[dict], tools: list[dict]):
    """One chat turn, cloud or local depending on `model`. Returns
    (content: str, tool_calls: list[dict]) where each tool_call is
    {"function": {"name": str, "arguments": dict}} — the same shape
    ollama's ChatResponse.message.tool_calls normalizes to, so existing
    per-specialist parsing code (tc.get("function", tc) / fn.get("name"))
    needs no change.
    """
    from clients import cloud_client
    if cloud_client.is_cloud_model(model):
        response = cloud_client.raw_completion(model=model, messages=messages, tools=tools)
    else:
        response = ollama_client.chat(
            model=model, messages=messages, tools=tools,
            options={"temperature": 0.1, "num_ctx": 8192},
        )
    msg = response.message
    content = (getattr(msg, "content", "") or "").strip()
    raw_tool_calls = getattr(msg, "tool_calls", None) or []
    tool_calls = []
    for tc in raw_tool_calls:
        name = tc.function.name
        args = tc.function.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                args = {}
        tool_calls.append({"function": {"name": name, "arguments": args}})
    return content, tool_calls
