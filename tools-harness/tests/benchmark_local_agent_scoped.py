"""
Scoped benchmark for the chat UI local-agent path.

This keeps all filesystem work inside /tmp so timings reflect model/tool latency,
not a large repository or home-directory scan.
"""

import argparse
import io
import json
import re
import shutil
import sys
import time
import uuid
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from harness import run
from tools.filesystem import (
    copy_file,
    create_directory,
    delete_file,
    find_files,
    list_directory,
    read_file,
    rename_file,
)


ROOT = Path("/tmp/g4l_local_agent_bench")
TOOL_RE = re.compile(r"\[tool(?:/text)?\]\s+([a-zA-Z_][\w]*)")


def reset_fixture() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    ROOT.mkdir(parents=True)
    (ROOT / "alpha.txt").write_text("secret_word=zircon\n", encoding="utf-8")
    (ROOT / "beta.log").write_text("log line\n", encoding="utf-8")
    (ROOT / "notes.md").write_text("# Notes\n", encoding="utf-8")
    (ROOT / "rename_me.txt").write_text("rename me\n", encoding="utf-8")
    (ROOT / "delete_me.txt").write_text("delete me\n", encoding="utf-8")
    (ROOT / "copy_src").mkdir()
    (ROOT / "copy_src" / "inside.txt").write_text("copy me\n", encoding="utf-8")


def task_defs() -> list[dict]:
    return [
        {
            "name": "list_directory",
            "query": f"List files in {ROOT}. Answer with the filenames.",
            "expected": {"list_directory"},
            "check": lambda reply: "alpha.txt" in reply and "beta.log" in reply,
        },
        {
            "name": "read_file",
            "query": f"Read {ROOT / 'alpha.txt'} and tell me the secret word.",
            "expected": {"read_file"},
            "check": lambda reply: "zircon" in reply.lower(),
        },
        {
            "name": "find_files",
            "query": f"Find markdown files under {ROOT}.",
            "expected": {"find_files", "list_directory", "file_tree"},
            "check": lambda reply: "notes.md" in reply,
        },
        {
            "name": "create_directory",
            "query": f"Create directory {ROOT / 'created_dir'}.",
            "expected": {"create_directory"},
            "check": lambda _reply: (ROOT / "created_dir").is_dir(),
        },
        {
            "name": "rename_file",
            "query": f"Rename {ROOT / 'rename_me.txt'} to {ROOT / 'renamed.txt'}.",
            "expected": {"rename_file"},
            "check": lambda _reply: (ROOT / "renamed.txt").exists() and not (ROOT / "rename_me.txt").exists(),
        },
        {
            "name": "delete_file",
            "query": f"Delete file {ROOT / 'delete_me.txt'}.",
            "expected": {"delete_file"},
            "check": lambda _reply: not (ROOT / "delete_me.txt").exists(),
        },
        {
            "name": "copy_file",
            "query": f"Copy folder {ROOT / 'copy_src'} to {ROOT / 'copy_dst'}.",
            "expected": {"copy_file"},
            "check": lambda _reply: (ROOT / "copy_dst" / "inside.txt").exists(),
        },
    ]


def run_harness_task(task: dict, model: str, stream: bool = True) -> dict:
    first_token_at = None

    def on_token(_token: str) -> None:
        nonlocal first_token_at
        if first_token_at is None:
            first_token_at = time.perf_counter()

    buf = io.StringIO()
    start = time.perf_counter()
    error = None
    try:
        with redirect_stdout(buf):
            kwargs = {
                "model": model,
                "chat_id": f"local_bench_{uuid.uuid4().hex[:10]}",
                "force_local_agent": True,
                "max_rounds": 5,
            }
            if stream:
                kwargs["on_token"] = on_token
            reply, routed_model, intent = run(task["query"], **kwargs)
    except Exception as exc:
        reply = str(exc)
        routed_model = model
        intent = "error"
        error = str(exc)
    elapsed = time.perf_counter() - start
    captured = buf.getvalue()
    tools = TOOL_RE.findall(captured)
    expected_hit = bool(set(tools) & task["expected"])
    direct_hit = not tools and intent == "filesystem"
    success = error is None and (expected_hit or direct_hit) and task["check"](reply)
    return {
        "task": task["name"],
        "elapsed_s": round(elapsed, 3),
        "first_token_s": round(first_token_at - start, 3) if first_token_at else None,
        "tools": tools,
        "expected_tool": sorted(task["expected"]),
        "success": success,
        "model": routed_model,
        "intent": intent,
        "reply": reply.strip().replace("\n", " ")[:220],
        "error": error,
    }


def run_raw_tool_tasks() -> list[dict]:
    tasks = [
        {
            "name": "list_directory",
            "call": lambda: list_directory(str(ROOT)),
            "check": lambda out: "alpha.txt" in out and "beta.log" in out,
        },
        {
            "name": "read_file",
            "call": lambda: read_file(str(ROOT / "alpha.txt")),
            "check": lambda out: "zircon" in out.lower(),
        },
        {
            "name": "find_files",
            "call": lambda: find_files("*.md", str(ROOT)),
            "check": lambda out: "notes.md" in out,
        },
        {
            "name": "create_directory",
            "call": lambda: create_directory(str(ROOT / "created_dir")),
            "check": lambda _out: (ROOT / "created_dir").is_dir(),
        },
        {
            "name": "rename_file",
            "call": lambda: rename_file(str(ROOT / "rename_me.txt"), str(ROOT / "renamed.txt")),
            "check": lambda _out: (ROOT / "renamed.txt").exists() and not (ROOT / "rename_me.txt").exists(),
        },
        {
            "name": "delete_file",
            "call": lambda: delete_file(str(ROOT / "delete_me.txt")),
            "check": lambda _out: not (ROOT / "delete_me.txt").exists(),
        },
        {
            "name": "copy_file",
            "call": lambda: copy_file(str(ROOT / "copy_src"), str(ROOT / "copy_dst"), recursive=True),
            "check": lambda _out: (ROOT / "copy_dst" / "inside.txt").exists(),
        },
    ]
    results = []
    for task in tasks:
        start = time.perf_counter()
        error = None
        try:
            output = task["call"]()
        except Exception as exc:
            output = str(exc)
            error = str(exc)
        elapsed = time.perf_counter() - start
        results.append(
            {
                "task": task["name"],
                "elapsed_s": round(elapsed, 4),
                "success": error is None and task["check"](output),
                "reply": str(output).strip().replace("\n", " ")[:220],
                "error": error,
            }
        )
    return results


def run_endpoint_probe(base_url: str, model: str) -> dict:
    import requests

    chat_id = f"local_endpoint_{uuid.uuid4().hex[:10]}"
    params = {
        "message": f"Read {ROOT / 'alpha.txt'} and tell me the secret word.",
        "chat_id": chat_id,
        "local_agent": "1",
    }
    if model:
        params["model"] = model

    start = time.perf_counter()
    first_event = None
    first_token = None
    done = False
    body = []
    with requests.get(f"{base_url.rstrip('/')}/chat/stream", params=params, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            now = time.perf_counter()
            if not raw:
                continue
            if first_event is None:
                first_event = now
            if raw.startswith("data: "):
                data = raw[6:]
                if data == "[DONE]":
                    done = True
                    break
                if first_token is None:
                    first_token = now
                body.append(data)
    elapsed = time.perf_counter() - start
    text = "".join(body)
    return {
        "endpoint": base_url,
        "elapsed_s": round(elapsed, 3),
        "first_event_s": round(first_event - start, 3) if first_event else None,
        "first_token_s": round(first_token - start, 3) if first_token else None,
        "done": done,
        "success": done and "zircon" in text.lower(),
        "reply": text.strip().replace("\n", " ")[:220],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemma4")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--raw-tools", action="store_true", help="Only time filesystem tools; skip LLM.")
    parser.add_argument("--no-stream", action="store_true", help="Use blocking Ollama chat for harness tasks.")
    args = parser.parse_args()

    reset_fixture()
    if args.raw_tools:
        results = run_raw_tool_tasks()
        passed = sum(1 for result in results if result["success"])
        total_time = sum(result["elapsed_s"] for result in results)
        print("local-agent raw filesystem tool benchmark")
        print(f"tools: {passed}/{len(results)} effective, total={total_time:.4f}s, avg={total_time / len(results):.4f}s")
        for result in results:
            status = "PASS" if result["success"] else "FAIL"
            print(f"{status:4s} {result['task']:16s} {result['elapsed_s']:8.4f}s")
            print(f"     reply={result['reply']}")
        print("json=" + json.dumps({"raw_tools": results}, sort_keys=True))
        return

    results = [run_harness_task(task, args.model, stream=not args.no_stream) for task in task_defs()]
    endpoint = run_endpoint_probe(args.endpoint, args.model) if args.endpoint else None

    passed = sum(1 for result in results if result["success"])
    total_time = sum(result["elapsed_s"] for result in results)
    print(f"local-agent scoped benchmark model={args.model}")
    print(f"harness: {passed}/{len(results)} effective, total={total_time:.3f}s, avg={total_time / len(results):.3f}s")
    for result in results:
        status = "PASS" if result["success"] else "FAIL"
        tools = ",".join(result["tools"]) or "none"
        first = result["first_token_s"] if result["first_token_s"] is not None else "-"
        print(f"{status:4s} {result['task']:16s} {result['elapsed_s']:7.3f}s first={first!s:>6s} tools={tools}")
        print(f"     reply={result['reply']}")
    if endpoint:
        print(
            "endpoint: "
            f"success={endpoint['success']} total={endpoint['elapsed_s']:.3f}s "
            f"first_event={endpoint['first_event_s']} first_token={endpoint['first_token_s']}"
        )
        print(f"     reply={endpoint['reply']}")
    print("json=" + json.dumps({"harness": results, "endpoint": endpoint}, sort_keys=True))


if __name__ == "__main__":
    main()
