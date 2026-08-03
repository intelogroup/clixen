#!/usr/bin/env python3
"""
Benchmark local models on code-purpose query tool use.

Scope:
- Context7 docs lookup via get_library_docs
- Exa technical search via tech_search
- Local open-source demo lookup via local_search on qwen_lancedb_demo

This measures:
1. tool selection
2. argument quality
3. tool execution success
4. grounded final answer quality

Usage:
    python3 tools-harness/benchmark_code_query_tools.py

Environment:
    CODE_QUERY_MODELS="qwen3.5:4b,qwen3:4b,qwen3:8b,gemma4:latest"
    OLLAMA_URL="http://127.0.0.1:11434/api/chat"
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")
load_dotenv(ROOT.parent / ".env.local")

from tools.registry import ALL_TOOLS, EXECUTORS  # noqa: E402


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
MODELS = [
    model.strip()
    for model in os.environ.get(
        "CODE_QUERY_MODELS",
        "qwen3.5:4b,qwen3:4b,qwen3:8b,gemma4:latest",
    ).split(",")
    if model.strip()
]

TOOL_NAMES = {"get_library_docs", "tech_search", "local_search"}
TOOLS = [tool for tool in ALL_TOOLS if tool["function"]["name"] in TOOL_NAMES]

ROUTER_SYSTEM = """You are a code-query tool router.
Choose exactly one tool call and nothing else.
Do not answer from memory.

Use:
- get_library_docs for official library/framework docs and API usage
- tech_search for current package versions, release notes, GitHub repos, technical docs on the web
- local_search for the local opensrc demo knowledge already indexed on this machine; when the user mentions the local opensrc demo, use sources ["qwen_lancedb_demo"]
"""

ANSWER_SYSTEM = """You answer only from the tool result.
Return exactly one JSON object. No markdown.
Do not invent facts.
Keep it concise and practical.

Schema:
{
  "message": "short grounded answer",
  "grounded": true
}
"""


def post_chat(payload: dict) -> dict:
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def _model_options(model: str) -> tuple[dict, str]:
    opts = {"temperature": 0, "num_predict": 260}
    user_prefix = ""
    if model.startswith("qwen3"):
        opts = {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "repetition_penalty": 1.05,
            "num_predict": 260,
        }
        user_prefix = "/no_think\n"
    return opts, user_prefix


def ask_tool(model: str, prompt: str) -> tuple[dict, float]:
    opts, user_prefix = _model_options(model)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": ROUTER_SYSTEM},
            {"role": "user", "content": user_prefix + prompt},
        ],
        "tools": TOOLS,
        "think": False if model.startswith("qwen3") else None,
        "stream": False,
        "options": opts,
    }
    started = time.time()
    raw = post_chat(payload)
    elapsed = time.time() - started
    return raw, elapsed


def ask_answer(model: str, user_prompt: str, tool_name: str, tool_output: str) -> tuple[dict | None, str, float]:
    opts, user_prefix = _model_options(model)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": ANSWER_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"{user_prefix}Original user question: {user_prompt}\n\n"
                    f"Tool used: {tool_name}\n\n"
                    f"Tool result:\n{tool_output[:5000]}"
                ),
            },
        ],
        "format": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "grounded": {"type": "boolean"},
            },
            "required": ["message", "grounded"],
        },
        "think": False if model.startswith("qwen3") else None,
        "stream": False,
        "options": opts,
    }
    started = time.time()
    raw = post_chat(payload)
    elapsed = time.time() - started
    content = raw.get("message", {}).get("content", "")
    try:
        return json.loads(content), content, elapsed
    except json.JSONDecodeError:
        return None, content, elapsed


def first_tool_call(raw: dict) -> tuple[str | None, dict]:
    tool_calls = raw.get("message", {}).get("tool_calls", []) or []
    if not tool_calls:
        return None, {}
    fn = tool_calls[0].get("function", {})
    args = fn.get("arguments", {}) or {}
    if isinstance(args, dict) and "function" in args and "arguments" in args:
        nested_name = args.get("function")
        nested_args = args.get("arguments", {}) or {}
        return nested_name or fn.get("name"), nested_args
    return fn.get("name"), args


def run_tool(tool_name: str, args: dict) -> str:
    if tool_name == "local_search":
        args = dict(args)
        args.setdefault("sources", ["qwen_lancedb_demo"])
        args.setdefault("top_k", 3)
    elif tool_name == "tech_search":
        args = dict(args)
        args.setdefault("max_results", 3)
    return EXECUTORS[tool_name](args)


def score_tool(case: dict, tool_name: str | None, args: dict, tool_output: str) -> tuple[int, list[str]]:
    notes = []
    if not tool_name:
        return 0, ["no_tool_call"]

    score = 1
    if tool_name == case["expected_tool"]:
        score += 3
    else:
        notes.append(f"tool={tool_name!r}")

    query_blob = json.dumps(args, sort_keys=True).lower()
    matched = sum(1 for term in case["arg_terms"] if term in query_blob)
    score += min(3, matched)
    if matched < min(2, len(case["arg_terms"])):
        notes.append(f"args={args!r}")

    if case["expected_tool"] == "local_search":
        sources = args.get("sources") or []
        if "qwen_lancedb_demo" in sources:
            score += 1
        else:
            notes.append(f"sources={sources!r}")
    else:
        score += 1

    lowered = (tool_output or "").lower()
    if not lowered or lowered.startswith("[tool error]") or lowered.startswith("embedding failed") or "traceback" in lowered:
        notes.append("tool_execution_failed")
    else:
        score += 2
    return score, notes


def score_answer(case: dict, answer: dict | None) -> tuple[int, list[str]]:
    notes = []
    if not isinstance(answer, dict):
        return 0, ["invalid_json"]

    score = 1
    message = str(answer.get("message", ""))
    lowered = message.lower()
    if 24 <= len(message) <= case["max_chars"]:
        score += 2
    else:
        notes.append(f"len={len(message)}")

    hit_count = sum(1 for term in case["answer_terms"] if term in lowered)
    score += min(4, hit_count)
    if hit_count < min(2, len(case["answer_terms"])):
        notes.append(f"missing_terms={case['answer_terms']}")

    if answer.get("grounded") is True:
        score += 1
    else:
        notes.append(f"grounded={answer.get('grounded')!r}")

    for bad in case.get("forbidden_terms", []):
        if bad in lowered:
            score -= 2
            notes.append(f"hallucinated={bad!r}")

    return max(0, score), notes


def main() -> int:
    cases = [
        {
            "name": "context7_fastapi_background",
            "user": "Using FastAPI, how do I run a background task without blocking the response?",
            "expected_tool": "get_library_docs",
            "arg_terms": ["fastapi", "background"],
            "answer_terms": ["background", "task", "fastapi"],
            "max_chars": 260,
            "forbidden_terms": [],
        },
        {
            "name": "context7_prisma_compound_id",
            "user": "In Prisma, how do I define a compound primary key?",
            "expected_tool": "get_library_docs",
            "arg_terms": ["prisma", "compound"],
            "answer_terms": ["prisma", "compound", "id"],
            "max_chars": 240,
            "forbidden_terms": [],
        },
        {
            "name": "exa_pydantic_release",
            "user": "What's the latest stable Pydantic version and release notes?",
            "expected_tool": "tech_search",
            "arg_terms": ["pydantic", "latest", "release"],
            "answer_terms": ["pydantic", "release"],
            "max_chars": 220,
            "forbidden_terms": [],
        },
        {
            "name": "exa_nextjs_release",
            "user": "What's the latest stable Next.js release and what changed in it?",
            "expected_tool": "tech_search",
            "arg_terms": ["next.js", "latest", "release"],
            "answer_terms": ["next", "release"],
            "max_chars": 220,
            "forbidden_terms": [],
        },
        {
            "name": "opensrc_zod_object",
            "user": "In the local opensrc Zod demo, how is z.object used?",
            "expected_tool": "local_search",
            "arg_terms": ["zod", "object", "qwen_lancedb_demo"],
            "answer_terms": ["zod", "object"],
            "max_chars": 220,
            "forbidden_terms": [],
        },
        {
            "name": "opensrc_zod_safeparse",
            "user": "In the local opensrc Zod demo, what does safeParse return on failure?",
            "expected_tool": "local_search",
            "arg_terms": ["safeparse", "zod", "qwen_lancedb_demo"],
            "answer_terms": ["safeparse", "failure"],
            "max_chars": 220,
            "forbidden_terms": [],
        },
    ]

    overall = []
    for model in MODELS:
        print(f"MODEL {model}", flush=True)
        total = 0
        latencies = []
        for case in cases:
            raw, tool_elapsed = ask_tool(model, case["user"])
            tool_name, args = first_tool_call(raw)
            tool_output = ""
            if tool_name in EXECUTORS:
                try:
                    tool_output = run_tool(tool_name, args)
                except Exception as exc:
                    tool_output = f"[tool error] {exc}"

            tool_score, tool_notes = score_tool(case, tool_name, args, tool_output)
            answer, answer_raw, answer_elapsed = (None, "", 0.0)
            if tool_output and not tool_output.startswith("[tool error]"):
                answer, answer_raw, answer_elapsed = ask_answer(
                    model, case["user"], tool_name, tool_output
                )
            answer_score, answer_notes = score_answer(case, answer)

            case_total = tool_score + answer_score
            total += case_total
            latencies.append(tool_elapsed + answer_elapsed)
            print(
                json.dumps(
                    {
                        "task": case["name"],
                        "score": case_total,
                        "max": 14,
                        "tool": tool_name,
                        "args": args,
                        "tool_seconds": round(tool_elapsed, 2),
                        "answer_seconds": round(answer_elapsed, 2),
                        "tool_notes": tool_notes,
                        "answer_notes": answer_notes,
                        "tool_preview": tool_output[:220],
                        "answer_preview": answer_raw[:220],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        row = {
            "model": model,
            "score": total,
            "max": len(cases) * 14,
            "pct": round(100 * total / (len(cases) * 14), 1),
            "avg_seconds": round(sum(latencies) / len(latencies), 2),
        }
        overall.append(row)
        print("SUMMARY_ROW " + json.dumps(row, sort_keys=True), flush=True)

    print("FINAL_SUMMARY", flush=True)
    for row in sorted(overall, key=lambda item: (item["score"], -item["avg_seconds"]), reverse=True):
        print(json.dumps(row, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
