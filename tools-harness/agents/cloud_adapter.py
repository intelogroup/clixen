"""
Live model adapter for the journal-driven run loop (M1).

`run_loop.run_rounds` owns tool execution and retries; it only needs a
`model_fn(messages, tool_names) -> {"text", "tool_calls"}`. This module
binds that contract to the real cloud model through
`cloud_client.raw_completion` — the public SINGLE-completion seam meant for
callers that run their own tool loop (the LangGraph local-agent uses it the
same way). `cloud_client.chat()` is deliberately NOT used: it would run a
second, hidden tool loop underneath the journal.

Everything the adapter returns is journal-shaped, so a resumed run rebuilds
identical provider messages; tool arguments cross the boundary as dicts
(the journal's native form) and are re-encoded on the way out by
`raw_completion`'s replay normalizer.
"""
from __future__ import annotations

import json

import clients.cloud_client as cc


def _decode_args(raw: str) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def make_model_fn(model: str, tools: list[dict], *, timeout: float | None = None):
    """Build the `model_fn` run_loop expects.

    tools: full OpenAI-style schemas; only the ones whose names appear in a
    given round's `tool_names` are offered, so an un-offered tool can never
    be called even if the model hallucinates it.

    No token streaming: `raw_completion` is non-streaming, and a journal run
    gets its progress from journal events (the run surface tails those), so
    per-delta callbacks would be redundant plumbing.
    """
    by_name = {t["function"]["name"]: t for t in (tools or [])
               if isinstance(t, dict) and "function" in t}

    def model_fn(messages: list, tool_names: list) -> dict:
        offered = [by_name[n] for n in (tool_names or []) if n in by_name] or None
        choice = cc.raw_completion(model, messages, offered, timeout=timeout)
        msg = getattr(choice, "message", None)
        text = (getattr(msg, "content", "") or "") if msg else ""
        calls = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            calls.append({"id": getattr(tc, "id", ""),
                          "name": getattr(fn, "name", ""),
                          "args": _decode_args(getattr(fn, "arguments", ""))})
        return {"text": text, "tool_calls": calls}

    return model_fn