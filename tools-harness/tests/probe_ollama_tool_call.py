"""
Minimal Ollama tool-call probe.

This bypasses harness prompts, routing, and filesystem tools. It only checks
whether the selected model emits structured tool_calls for a simple function.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import ollama


TOOL = {
    "type": "function",
    "function": {
        "name": "list_directory",
        "description": "List files in a local directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemma4")
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()

    messages = [
        {
            "role": "system",
            "content": "You have tools. For directory listing requests, call list_directory. Do not answer in text.",
        },
        {
            "role": "user",
            "content": "List files in /tmp/g4l_local_agent_bench.",
        },
    ]
    start = time.perf_counter()
    if args.stream:
        chunks = []
        tool_calls = None
        for chunk in ollama.chat(
            model=args.model,
            messages=messages,
            tools=[TOOL],
            stream=True,
            keep_alive=-1,
        ):
            if chunk.message.content:
                chunks.append(chunk.message.content)
            if chunk.message.tool_calls:
                tool_calls = chunk.message.tool_calls
        elapsed = time.perf_counter() - start
        print(
            json.dumps(
                {
                    "model": args.model,
                    "stream": True,
                    "elapsed_s": round(elapsed, 3),
                    "content": "".join(chunks),
                    "tool_calls": [
                        {"name": c.function.name, "arguments": c.function.arguments} for c in (tool_calls or [])
                    ],
                },
                sort_keys=True,
            )
        )
        return

    resp = ollama.chat(
        model=args.model,
        messages=messages,
        tools=[TOOL],
        stream=False,
        keep_alive=-1,
    )
    elapsed = time.perf_counter() - start
    tool_calls = resp.message.tool_calls or []
    print(
        json.dumps(
            {
                "model": args.model,
                "stream": False,
                "elapsed_s": round(elapsed, 3),
                "content": resp.message.content or "",
                "tool_calls": [
                    {"name": c.function.name, "arguments": c.function.arguments} for c in tool_calls
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
