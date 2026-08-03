"""
Fallback model benchmark — qwen3:4b vs qwen3:8b vs qwen3.5:9b.

Tests each model on TASK_ROUTING-style automation flows and reports:
- tool selection accuracy
- instruction following (validator pass rate)
- error rate

Latency is NOT a primary metric — we care about capability and accuracy.

Usage:
    python3 tools-harness/benchmark_fallback_models.py
    MODELS="qwen3:4b,qwen3:8b" python3 tools-harness/benchmark_fallback_models.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from clients.ollama_client import _get_client, _MODEL_NUM_CTX

C = {
    "g": "\033[32m", "r": "\033[31m", "y": "\033[33m",
    "c": "\033[36m", "m": "\033[35m", "b": "\033[1m",
    "d": "\033[2m", "x": "\033[0m",
}

MODELS = [
    m.strip()
    for m in os.environ.get("MODELS", "qwen3:4b,qwen3:8b,qwen3.5:9b").split(",")
    if m.strip()
]

# ── Tool definitions ─────────────────────────────────────────────────────

TOOL_DEFS = {
    "get_current_time": {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current date and time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "list_calendar_events": {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": "List calendar events for a given date range. Call this to check what's on your calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date to check events for, e.g. 'today' or 'tomorrow' or a specific date.",
                    },
                },
            },
        },
    },
    "list_tasks": {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List all current tasks from the task list.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "create_task": {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new task with a title and optional due date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "due": {"type": "string", "description": "Optional due date"},
                },
                "required": ["title"],
            },
        },
    },
    "list_emails": {
        "type": "function",
        "function": {
            "name": "list_emails",
            "description": "List recent emails from the inbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max": {"type": "integer", "description": "Max emails to return"},
                },
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file at the given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                },
                "required": ["path"],
            },
        },
    },
    "set_reminder": {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Set a reminder for a specific time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Reminder text"},
                    "time": {"type": "string", "description": "Time for the reminder"},
                },
                "required": ["text", "time"],
            },
        },
    },
    "send_telegram": {
        "type": "function",
        "function": {
            "name": "send_telegram",
            "description": "Send a message via Telegram to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to send"},
                },
                "required": ["message"],
            },
        },
    },
}


# ── Test Cases ──────────────────────────────────────────────────────────

def _any_of(*items: str) -> callable:
    return lambda r: any(i.lower() in (r or "").lower() for i in items)


def _all_of(*items: str) -> callable:
    return lambda r: all(i.lower() in (r or "").lower() for i in items)


CASES = [
    {
        "name": "morning_briefing",
        "tools": ["get_current_time", "list_calendar_events", "list_tasks",
                   "list_emails", "send_telegram"],
        "query": "Check my calendar for today, list my tasks, look at my recent emails, and send me a Telegram summary.",
        "expected_tools": {"list_calendar_events", "list_tasks", "send_telegram"},
        "validator": _any_of("calendar", "task", "telegram"),
    },
    {
        "name": "email_check",
        "tools": ["get_current_time", "list_emails", "create_task", "send_telegram"],
        "query": "Check my recent emails and create tasks for anything urgent.",
        "expected_tools": {"list_emails", "create_task"},
        "validator": _any_of("email", "task", "urgent"),
    },
    {
        "name": "calendar_check",
        "tools": ["get_current_time", "list_calendar_events", "send_telegram"],
        "query": "What's on my calendar today? Send me the highlights.",
        "expected_tools": {"list_calendar_events", "send_telegram"},
        "validator": _any_of("calendar", "event", "meeting", "highlights"),
    },
    {
        "name": "task_ops",
        "tools": ["get_current_time", "list_tasks", "create_task", "send_telegram"],
        "query": "Show me my current tasks, then add a new task: pick up dry cleaning tomorrow at 5pm.",
        "expected_tools": {"list_tasks", "create_task"},
        "validator": _any_of("task", "dry clean"),
    },
    {
        "name": "knowledge_to_action",
        "tools": ["read_file", "create_task", "send_telegram"],
        "query": "Read the file /tmp/readme.md and create tasks from any action items you find.",
        "expected_tools": {"read_file", "create_task"},
        "validator": _any_of("task", "action", "readme"),
    },
    {
        "name": "reminder",
        "tools": ["get_current_time", "set_reminder", "send_telegram"],
        "query": "Set a reminder for tomorrow morning at 9am to review the PR.",
        "expected_tools": {"set_reminder"},
        "validator": _any_of("reminder", "9", "morning", "pr"),
    },
]


# ── Helpers ─────────────────────────────────────────────────────────────

def _build_qwen_tools(tool_names: list[str]) -> list[dict]:
    return [TOOL_DEFS[n] for n in tool_names]


def _call_ollama(model: str, messages: list, tools: list[str] | None = None,
                 temperature: float | None = None) -> tuple:
    """Call Ollama using the same client the harness uses."""
    client = _get_client()
    opts = {}
    num_ctx = _MODEL_NUM_CTX.get(model) or next(
        (v for k, v in _MODEL_NUM_CTX.items() if model.startswith(k)), 8192
    )
    opts["num_ctx"] = num_ctx

    # Qwen3 tool-calling params (from ollama_client.py)
    _is_qwen3 = model.startswith("qwen3")
    if _is_qwen3 and tools:
        opts.setdefault("temperature", 0.7)
        opts.setdefault("top_p", 0.8)
        opts.setdefault("top_k", 20)
        opts["repetition_penalty"] = 1.05
    elif temperature is not None:
        opts["temperature"] = temperature

    _think = False if (_is_qwen3 and tools) else None

    tool_defs = _build_qwen_tools(tools) if tools else None

    resp = client.chat(
        model=model,
        messages=messages,
        tools=tool_defs,
        keep_alive=-1,
        options=opts or None,
        think=_think,
    )
    return resp, opts


def _simulate_tool_result(tname: str) -> str:
    results = {
        "get_current_time": "Current time: 2026-05-21 14:30:00 EDT",
        "list_calendar_events": "Events today: 1) Team standup at 10am, 2) Lunch with Sarah at 12:30pm, 3) PR review at 3pm",
        "list_tasks": "Tasks: 1) Review PR #42, 2) Update README, 3) Deploy v2.1",
        "list_emails": "Recent emails: 1) Urgent: Server maintenance tonight, 2) Meeting notes from yesterday, 3) Newsletter",
        "read_file": "Contents: TODO: Update API docs. TODO: Review PR #42.",
        "create_task": "Task created successfully.",
        "send_telegram": "Telegram message sent.",
        "set_reminder": "Reminder set successfully.",
    }
    return results.get(tname, f"[OK] {tname} completed.")


# ── Runner ──────────────────────────────────────────────────────────────

def run_case(model: str, case: dict) -> dict:
    tools_called: list[str] = []
    final_response = ""
    error = ""
    t0 = time.time()

    tool_names = case["tools"]

    # Qwen3 needs a clear system prompt that lists tool capabilities
    # without forcing a specific structure. Hermes-style tool use is default.
    tool_desc = "\n".join(f"- {t}: {TOOL_DEFS[t]['function']['description']}" for t in tool_names)
    system = (
        "You are an automation assistant. You have access to the following tools:\n"
        f"{tool_desc}\n\n"
        "Use these tools to fulfill the user's request. Think step by step about what information "
        "you need, call the appropriate tools in order, then summarize the results for the user. "
        "If a tool call returns data, use it to decide your next step. "
        "When you have all the information you need, give a clear, concise response."
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": case["query"]},
    ]

    max_rounds = 6

    try:
        for step in range(max_rounds):
            resp, _ = _call_ollama(model, messages, tools=tool_names)
            msg = resp.get("message", {})

            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls", [])

            # Check if we need to handle thinking content
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
            if reasoning and not content and not tool_calls:
                # Still in reasoning phase, continue
                messages.append({"role": "assistant", "content": "", "reasoning": reasoning})
                continue

            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    tname = fn.get("name", "")
                    if tname and tname in tool_names:
                        tools_called.append(tname)
                        # Add assistant message with tool calls
                        messages.append({
                            "role": "assistant",
                            "content": content or "",
                            "tool_calls": [tc],
                        })
                        # Add tool result
                        result = _simulate_tool_result(tname)
                        messages.append({
                            "role": "tool",
                            "content": result,
                            "name": tname,
                        })
            else:
                final_response = content
                break
        else:
            if not final_response:
                error = "exceeded max_rounds without final response"

    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    elapsed = time.time() - t0
    tool_ok = bool(case["expected_tools"] & set(tools_called))
    validator_ok = bool(case["validator"](final_response)) if not error else False

    return {
        "model": model,
        "case": case["name"],
        "tools_called": tools_called,
        "final_response": final_response[:400] if final_response else "",
        "elapsed_s": round(elapsed, 1),
        "tool_ok": tool_ok,
        "validator_ok": validator_ok,
        "passed": tool_ok and validator_ok and not error,
        "error": error,
    }


# ── Reporting ───────────────────────────────────────────────────────────

def print_results(results: list[dict]):
    models_seen = {}
    for r in results:
        m = r["model"]
        if m not in models_seen:
            models_seen[m] = {"n": 0, "pass": 0, "elapsed": 0,
                              "tool_ok": 0, "val_ok": 0,
                              "tool_calls": 0, "no_tool": 0}
        d = models_seen[m]
        d["n"] += 1
        if r["passed"]:
            d["pass"] += 1
        d["elapsed"] += r["elapsed_s"]
        if r["tool_ok"]:
            d["tool_ok"] += 1
        if r["validator_ok"]:
            d["val_ok"] += 1
        if r["tools_called"]:
            d["tool_calls"] += 1
        else:
            d["no_tool"] += 1

    print(f"\n{C['b']}── Summary ──{C['x']}")
    print(f"{'Model':<20} {'Cases':>6} {'Pass':>5} {'Tool%':>7} {'Val%':>7} {'NoTool':>7} {'Avg(s)':>7}")
    for m, d in sorted(models_seen.items()):
        t_avg = d["elapsed"] / d["n"] if d["n"] else 0
        tp = d["tool_ok"] / d["n"] * 100
        vp = d["val_ok"] / d["n"] * 100
        print(f"{m:<20} {d['n']:>6} {d['pass']:>5} {tp:>6.0f}% {vp:>6.0f}% {d['no_tool']:>7} {t_avg:>6.1f}")

    print(f"\n{C['b']}── Detail ──{C['x']}")
    for r in results:
        status = f"{C['g']}PASS{C['x']}" if r["passed"] else f"{C['r']}FAIL{C['x']}"
        print(f"  {r['model']:<20} {r['case']:<25} {status}  ({r['elapsed_s']}s)")
        if r["tools_called"]:
            print(f"    tools: {', '.join(r['tools_called'])}")
        else:
            print("    tools: (none)")
        if r["error"]:
            print(f"    error: {r['error']}")
        resp = r.get("final_response", "")
        if resp:
            print(f"    resp: {resp[:200]}")
        print()


def save_results(results: list[dict], path: str = "benchmark_fallback_results.json"):
    with open(ROOT / path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Results saved to {path}")


# ── Main ────────────────────────────────────────────────────────────────

def main():
    print(f"{C['b']}Fallback Model Benchmark{C['x']}")
    print(f"  Models: {', '.join(MODELS)}")
    print(f"  Cases: {len(CASES)}")
    print()

    all_results = []
    client = _get_client()

    for model in MODELS:
        print(f"{C['c']}═══ {model} ═══{C['x']}")

        # Warmup
        print(f"  Warming up {model}...", end=" ", flush=True)
        try:
            client.chat(model=model, messages=[{"role": "user", "content": "ok"}],
                        options={"num_predict": 1, "num_ctx": 4096}, think=False if model.startswith("qwen3") else None)
            print("OK")
        except Exception as e:
            print(f"FAIL: {e}")
            continue

        for case in CASES:
            print(f"  {case['name']}...", end=" ", flush=True)
            result = run_case(model, case)
            all_results.append(result)
            status = f"{C['g']}PASS{C['x']}" if result["passed"] else f"{C['r']}FAIL{C['x']}"
            tools_str = ", ".join(result["tools_called"]) if result["tools_called"] else "no tools"
            print(f" {status} tools=[{tools_str}] ({result['elapsed_s']}s)")
            if result["error"]:
                print(f"    error: {result['error']}")

    if all_results:
        print_results(all_results)
        save_results(all_results)
    else:
        print("No results collected.")


if __name__ == "__main__":
    main()
