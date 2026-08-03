#!/usr/bin/env python3
"""
Repo-specific code/codebase benchmark for local Ollama models.

Focus:
- code comprehension on real files in this repo
- cross-file reasoning
- bounded code generation that can be executed against asserts

Usage:
    python3 tools-harness/benchmark_code_models.py

Environment:
    CODE_BENCH_MODELS="gemma4:latest,phi4-mini:latest,mistral-nemo:latest"
    OLLAMA_URL="http://127.0.0.1:11434/api/chat"
"""

from __future__ import annotations

import json
import os
import time
import urllib.request


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
MODELS = [
    model.strip()
    for model in os.environ.get(
        "CODE_BENCH_MODELS",
        "gemma4:latest,phi4-mini:latest,mistral-nemo:latest",
    ).split(",")
    if model.strip()
]

HARNESS_SNIPPET = """
elif intent == "email":
    # get_latest_email is a composite (list+read in one call) — reduces the
    # chain to 2 steps for nemo: get_latest_email → send_telegram.
    active_tools = _tool(
        "get_latest_email", "list_emails", "read_email", "send_email",
        "send_telegram", "refresh_google_token",
    )
elif intent in ("code_heavy", "math"):
    if _chat_mode:
        active_tools = []
    else:
        active_tools = _FS_TOOL_SCHEMAS
        routed_model = "gemma4"
"""

ROUTER_SNIPPET = """
# Heavy code tasks (implement/refactor/architecture/tests) → gemma4
if _CODE_HEAVY_RE.search(msg):
    return "gemma4", "code_heavy"

def classify_ide(query: str) -> tuple[str, str]:
    _, intent = classify(query)
    if intent in ("git", "repl", "filesystem"):
        return "qwen3:8b", intent
    elif intent in ("code_heavy", "math", "library_docs"):
        return "qwen3:8b", intent
    elif intent in ("code_medium", "analysis"):
        return "qwen3:8b", intent
    else:
        return "qwen3:8b", "ide"
"""

REMINDER_SNIPPET = """
Required .env:
TELEGRAM_OWNER_CHAT_ID must be set in .env.

parameters required:
  text
  iso_datetime

if dt <= now:
    return f"[reminder error] Datetime '{iso_datetime}' is in the past."
"""

GEN_SYSTEM = """You are generating Python for an existing codebase.
Return only valid Python code. No markdown fences. No explanation.
Keep the implementation compact and deterministic.
"""

JSON_SYSTEM = """You answer questions about code snippets.
Return exactly one JSON object. No markdown. No prose outside JSON.
"""


def post_chat(payload: dict) -> dict:
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def ask_json(model: str, prompt: str, schema: dict) -> tuple[dict | None, str, float]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": JSON_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "think": False,
        "format": schema,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 400},
    }
    started = time.time()
    raw = post_chat(payload)
    elapsed = time.time() - started
    content = raw.get("message", {}).get("content", "")
    try:
        return json.loads(content), content, elapsed
    except json.JSONDecodeError:
        return None, content, elapsed


def ask_code(model: str, prompt: str) -> tuple[str, float]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": GEN_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "think": False,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 500},
    }
    started = time.time()
    raw = post_chat(payload)
    elapsed = time.time() - started
    return raw.get("message", {}).get("content", ""), elapsed


def score_email_tools(answer: dict | None) -> tuple[int, list[str]]:
    notes = []
    if not isinstance(answer, dict):
        return 0, ["invalid_json"]
    score = 2
    tools = set(answer.get("tools", []))
    expected = {
        "get_latest_email",
        "list_emails",
        "read_email",
        "send_email",
        "send_telegram",
        "refresh_google_token",
    }
    if tools == expected:
        score += 4
    else:
        notes.append(f"tools={sorted(tools)}")
    optimization = str(answer.get("optimization", "")).lower()
    if (
        "get_latest_email" in optimization
        and "send_telegram" in optimization
        and (
            "2 step" in optimization
            or "2-step" in optimization
            or "two step" in optimization
        )
    ):
        score += 2
    else:
        notes.append("optimization_missing")
    if answer.get("model_for_email") in {"mistral-nemo", "mistral-nemo:latest"}:
        score += 2
    else:
        notes.append(f"model_for_email={answer.get('model_for_email')!r}")
    return score, notes


def score_reminder_contract(answer: dict | None) -> tuple[int, list[str]]:
    notes = []
    if not isinstance(answer, dict):
        return 0, ["invalid_json"]
    score = 2
    if answer.get("required_env") == "TELEGRAM_OWNER_CHAT_ID":
        score += 2
    else:
        notes.append(f"required_env={answer.get('required_env')!r}")
    args = answer.get("required_args", [])
    if args == ["text", "iso_datetime"] or args == ["iso_datetime", "text"]:
        score += 3
    else:
        notes.append(f"required_args={args!r}")
    past_behavior = str(answer.get("past_datetime_behavior", "")).lower()
    if "error" in past_behavior and "past" in past_behavior:
        score += 3
    else:
        notes.append("past_behavior_missing")
    return score, notes


def score_code_route(answer: dict | None) -> tuple[int, list[str]]:
    notes = []
    if not isinstance(answer, dict):
        return 0, ["invalid_json"]
    score = 2
    if answer.get("chat_model") == "gemma4":
        score += 2
    else:
        notes.append(f"chat_model={answer.get('chat_model')!r}")
    if answer.get("ide_model") == "gemma4":
        score += 2
    else:
        notes.append(f"ide_model={answer.get('ide_model')!r}")
    chat_tools = answer.get("chat_tools")
    if chat_tools == []:
        score += 2
    else:
        notes.append(f"chat_tools={chat_tools!r}")
    ide_tools = answer.get("ide_tools")
    if ide_tools == "filesystem_read_tools":
        score += 2
    else:
        notes.append(f"ide_tools={ide_tools!r}")
    return score, notes


def run_generation_eval(code: str) -> tuple[int, list[str]]:
    notes = []
    namespace: dict = {}
    try:
        exec(code, namespace, namespace)
    except Exception as exc:
        return 0, [f"compile_error={type(exc).__name__}: {exc}"]

    fn = namespace.get("collect_missing_fields")
    if not callable(fn):
        return 0, ["missing_function"]

    score = 2
    try:
        out1 = fn(
            {"title": "Call Alex", "due_at": "unknown"},
            ("title", "due_at"),
            {"unknown", "now", ""},
        )
        if out1 == ["due_at"]:
            score += 3
        else:
            notes.append(f"case1={out1!r}")

        out2 = fn(
            {
                "table": "reminders",
                "row": {"title": "Pay rent", "due_at": "2026-05-03T10:00:00-04:00"},
            },
            ("table", "row", "title", "due_at"),
            {"unknown", "now", ""},
        )
        if out2 == []:
            score += 3
        else:
            notes.append(f"case2={out2!r}")

        out3 = fn(
            {"channel": "whatsapp", "recipient": "user", "message": "hi"},
            ("channel", "recipient", "message"),
            {"unknown", "user", ""},
        )
        if out3 == ["recipient"]:
            score += 2
        else:
            notes.append(f"case3={out3!r}")
    except Exception as exc:
        notes.append(f"runtime_error={type(exc).__name__}: {exc}")

    return score, notes


def main() -> int:
    tasks = [
        {
            "name": "email_tools",
            "kind": "json",
            "schema": {
                "type": "object",
                "properties": {
                    "model_for_email": {"type": "string"},
                    "tools": {"type": "array", "items": {"type": "string"}},
                    "optimization": {"type": "string"},
                },
                "required": ["model_for_email", "tools", "optimization"],
            },
            "prompt": (
                "Given this repo code snippet, answer with JSON.\n\n"
                f"{HARNESS_SNIPPET}\n\n"
                "Question: for the email intent, which tools are activated, which routed model handles it, "
                "and what optimization comment explains the chain reduction?"
            ),
            "scorer": score_email_tools,
        },
        {
            "name": "reminder_contract",
            "kind": "json",
            "schema": {
                "type": "object",
                "properties": {
                    "required_env": {"type": "string"},
                    "required_args": {"type": "array", "items": {"type": "string"}},
                    "past_datetime_behavior": {"type": "string"},
                },
                "required": ["required_env", "required_args", "past_datetime_behavior"],
            },
            "prompt": (
                "Given this repo code snippet, answer with JSON.\n\n"
                f"{REMINDER_SNIPPET}\n\n"
                "Question: which environment variable must be set, which arguments are required by the tool schema, "
                "and what happens if the datetime is in the past?"
            ),
            "scorer": score_reminder_contract,
        },
        {
            "name": "code_route",
            "kind": "json",
            "schema": {
                "type": "object",
                "properties": {
                    "chat_model": {"type": "string"},
                    "ide_model": {"type": "string"},
                    "chat_tools": {"type": "array", "items": {}},
                    "ide_tools": {"type": "string"},
                },
                "required": ["chat_model", "ide_model", "chat_tools", "ide_tools"],
            },
            "prompt": (
                "Given these repo code snippets, answer with JSON.\n\n"
                f"{HARNESS_SNIPPET}\n\n{ROUTER_SNIPPET}\n\n"
                "Question: for intent code_heavy, what model handles chat mode, what model handles IDE mode, "
                "what tools are active in chat mode, and what category of tools is active in IDE mode?"
            ),
            "scorer": score_code_route,
        },
        {
            "name": "generation_missing_fields",
            "kind": "code",
            "prompt": (
                "Write a Python function named collect_missing_fields(args, required_fields, bad_values).\n"
                "Rules:\n"
                "- args is a nested dict.\n"
                "- Return a list of required field names that are missing anywhere in the dict or have a bad string value.\n"
                "- Search nested dicts recursively.\n"
                "- Treat None as missing.\n"
                "- Preserve the order from required_fields.\n"
                "- Use only Python standard library.\n"
                "- Return only Python code."
            ),
            "scorer": run_generation_eval,
        },
    ]

    overall = []
    for model in MODELS:
        print(f"MODEL {model}", flush=True)
        total = 0
        max_total = 0
        for task in tasks:
            if task["kind"] == "json":
                parsed, raw, elapsed = ask_json(model, task["prompt"], task["schema"])
                score, notes = task["scorer"](parsed)
            else:
                raw, elapsed = ask_code(model, task["prompt"])
                score, notes = task["scorer"](raw)
            task_max = 10
            total += score
            max_total += task_max
            print(
                json.dumps(
                    {
                        "task": task["name"],
                        "score": score,
                        "max": task_max,
                        "seconds": round(elapsed, 2),
                        "notes": notes,
                        "preview": (raw[:300] if isinstance(raw, str) else raw),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        row = {
            "model": model,
            "score": total,
            "max": max_total,
            "pct": round(100 * total / max_total, 1),
        }
        overall.append(row)
        print("SUMMARY_ROW " + json.dumps(row, sort_keys=True), flush=True)

    print("FINAL_SUMMARY", flush=True)
    for row in sorted(overall, key=lambda item: item["score"], reverse=True):
        print(json.dumps(row, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
