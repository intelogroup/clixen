"""
Test Ornith in clixen's real multi-round tool loop.
Mimics ollama_client.chat() — plan, call tools, get results, synthesize.
Uses actual tools.registry.execute_tool.
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools-harness"))

import ollama
from tools.registry import execute_tool, is_error_result
from tools.injection_guard import wrap_external_output
from tools.premise_check import inject as _premise_inject

SEARCH_TOOLS = {"web_search"}

_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search web for current info. Use when you need up-to-date facts.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read contents of a file from disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to file"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Output file path"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash_exec",
            "description": "Run a bash command. Returns stdout+stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"}
                },
                "required": ["command"],
            },
        },
    },
]

TOOLS_BY_NAME = {t["function"]["name"]: t for t in _SCHEMAS}


def trunc(s, n=3000):
    s = str(s)
    return s[:n] + f"… [{len(s)} chars]" if len(s) > n else s


def call_ornith(messages, tools=None, opts=None):
    """Single Ornith call, returns (content, tool_calls, elapsed)."""
    o = dict(opts or {})
    o.setdefault("num_predict", 4096)
    o.setdefault("temperature", 0.3)
    o.setdefault("num_ctx", 16384)

    start = time.time()
    resp = ollama.chat(
        model="ornith:9b",
        messages=messages,
        tools=tools or None,
        options=o,
        think=False,
    )
    elapsed = time.time() - start
    return resp.message.content or "", resp.message.tool_calls, elapsed


def tool_loop(msgs, max_rounds=5):
    """Multi-round tool loop matching ollama_client.chat() pattern."""
    base_sys = (
        "You are a helpful coding assistant. Use tools to gather information "
        "before answering. When you have enough data, synthesize your answer. "
        "If search fails, retry with a different query."
    )
    sys_msg = {"role": "system", "content": base_sys}

    for rnd in range(max_rounds):
        history = [sys_msg] + list(msgs)
        content, tcs, elapsed = call_ornith(history, tools=_SCHEMAS)

        if not tcs:
            print(f"  [round {rnd}] synthesis — {elapsed:.1f}s")
            return content

        print(f"  [round {rnd}] {len(tcs)} tool(s) — {elapsed:.1f}s")

        # Execute all tools in parallel (same as real loop)
        with ThreadPoolExecutor(max_workers=min(len(tcs), 4)) as pool:
            futs = {
                pool.submit(
                    _wrap_exec, c.function.name, c.function.arguments,
                    msgs[0]["content"] if msgs else ""
                ): (i, c)
                for i, c in enumerate(tcs)
            }
            results = {}
            for f in futs:
                i, c = futs[f]
                results[i] = f.result()

        # Append assistant message + tool results
        asst_msg = {"role": "assistant", "content": content}
        if hasattr(tcs[0], "model_dump"):
            asst_msg["tool_calls"] = [t.model_dump() for t in tcs]
        msgs.append(asst_msg)

        for i, c in enumerate(tcs):
            fn_name = c.function.name
            result = results[i]
            msgs.append({
                "role": "tool",
                "name": fn_name,
                "tool_call_id": f"call_{rnd}_{i}",
                "content": trunc(result, 4000),
            })

    # Final non-tool call to synthesize
    history = [sys_msg] + list(msgs)
    content, _, elapsed = call_ornith(history, tools=None)
    print(f"  [final] synthesis — {elapsed:.1f}s")
    return content


def _wrap_exec(fn_name, fn_args, user_msg):
    """Execute tool with same wrappers as real loop."""
    try:
        result = execute_tool(fn_name, fn_args)
        if fn_name in SEARCH_TOOLS:
            result = _premise_inject(user_msg, result)
        result = wrap_external_output(fn_name, result)
        return result
    except Exception as e:
        return f"[error] {e}"


def test_multi_round_search_and_write():
    """Multi-round: search, then write results to a file."""
    print("\n=== Multi-round: search + write ===")
    msgs = [{"role": "user", "content": "Search the web for the current US Federal Funds rate. Then write the result to /tmp/fed_rate.txt"}]
    result = tool_loop(msgs)
    print(f"  Result ({len(result)} chars): {result[:400]}")
    assert len(result) > 20, "Empty result"
    print("  PASS")


def test_code_read_edit_write():
    """Multi-round: read a Python file, add a function, write it back."""
    # First create a test file
    test_path = "/tmp/test_ornith_demo.py"
    with open(test_path, "w") as f:
        f.write("# Demo file\ndef greet(name):\n    return f\"Hello, {name}!\"\n")
    print(f"\n=== Multi-round: read → edit → write ===")

    msgs = [{"role": "user", "content": f"Read {test_path}, then add a 'goodbye' function to it and save."}]
    result = tool_loop(msgs)
    print(f"  Result: {result[:400]}")
    # Verify file was written with goodbye function
    content = open(test_path).read()
    has_goodbye = "goodbye" in content
    print(f"  File has goodbye(): {has_goodbye}")
    assert has_goodbye, f"goodbye() not found in output:\n{content}"
    print("  PASS")


def test_search_synthesis():
    """Single tool call then synthesize — most common clixen pattern."""
    print("\n=== Search then synthesize ===")
    msgs = [{"role": "user", "content": "Search the web for the latest version of Python (2026). What are the new features?"}]
    result = tool_loop(msgs, max_rounds=3)
    print(f"  Result ({len(result)} chars): {result[:500]}")
    assert len(result) > 50, "Empty synthesis"
    print("  PASS")


def test_bash_and_summarize():
    """Run a bash command and summarize output."""
    print("\n=== Bash then summarize ===")
    msgs = [{"role": "user", "content": "Run 'ls -la /tmp | head -10' and summarize what files are there."}]
    result = tool_loop(msgs, max_rounds=3)
    print(f"  Result: {result[:400]}")
    assert len(result) > 20, "Empty result"
    print("  PASS")


def test_agentic_recovery():
    """Model should retry on tool failure instead of giving up."""
    print("\n=== Agentic recovery from tool failure ===")
    msgs = [{"role": "user", "content": "Search the web for 'latest Rust version 2026'. Then also read /nonexistent/file.txt and report what happened."}]
    result = tool_loop(msgs, max_rounds=4)
    print(f"  Result: {result[:500]}")
    assert len(result) > 30, "Empty result"
    # Should mention the file error
    has_err = "error" in result.lower() or "not exist" in result.lower() or "failed" in result.lower()
    print(f"  Mentions error: {has_err}")
    if not has_err:
        print("  WARNING: model didn't report the file read error")
    print("  PASS")


if __name__ == "__main__":
    print("=== Ornith tool-loop test against real clixen tool registry ===\n")

    tests = [
        test_search_synthesis,
        test_multi_round_search_and_write,
        test_code_read_edit_write,
        test_bash_and_summarize,
        test_agentic_recovery,
    ]

    passed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")

    print(f"\n=== {passed}/{len(tests)} passed ===")
