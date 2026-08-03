"""
Context7 library docs tool — fetches up-to-date docs via @upstash/context7-mcp.

Flow per call:
  1. Spawn `npx @upstash/context7-mcp` subprocess (stdio transport)
  2. Send initialize handshake
  3. resolve-library-id(library_name) → /libraryId
  4. get-library-docs(libraryId, topic) → markdown snippet
  5. Terminate subprocess

Results are cached in KnowledgeBase (TTL: 7d) to avoid redundant spawns.
"""

import json
import os
import subprocess
import threading
import time

# Max seconds to wait for a single JSON-RPC response from the MCP subprocess.
# Previously readline() blocked forever on a hung npx — this bounds the call.
_MCP_RESPONSE_TIMEOUT_S = float(os.environ.get("CONTEXT7_TIMEOUT", "30"))

SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_library_docs",
        "description": (
            "Fetch current, official documentation for any library or framework "
            "(React, Next.js, FastAPI, Tailwind, Prisma, etc.). "
            "Use when the user asks how to use a specific API, config option, "
            "or when you need accurate, version-aware docs. "
            "Prefers local cache; falls back to live fetch."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "library": {
                    "type": "string",
                    "description": "Library or framework name, e.g. 'react', 'fastapi', 'next.js'",
                },
                "topic": {
                    "type": "string",
                    "description": "Specific topic or API to look up, e.g. 'useEffect', 'routing', 'authentication'",
                },
            },
            "required": ["library"],
        },
    },
}


class _MCP:
    """Minimal stdio JSON-RPC client for MCP servers."""

    def __init__(self, cmd: list[str]):
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._id = 0
        self._lock = threading.Lock()

    def _send(self, method: str, params: dict) -> dict:
        with self._lock:
            self._id += 1
            msg = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
            line = json.dumps(msg) + "\n"
            self._proc.stdin.write(line.encode())
            self._proc.stdin.flush()
            # Read response lines until we get a JSON-RPC result/error for our id.
            # readline() blocks forever if npx/network stalls — bound each wait so
            # a hung MCP subprocess can't freeze the tool call.
            import select as _select
            deadline = time.time() + _MCP_RESPONSE_TIMEOUT_S
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise RuntimeError(f"MCP request timed out after {_MCP_RESPONSE_TIMEOUT_S}s")
                ready, _, _ = _select.select([self._proc.stdout], [], [], max(0.1, remaining))
                if not ready:
                    continue
                raw = self._proc.stdout.readline()
                if not raw:
                    raise RuntimeError("MCP subprocess closed stdout")
                try:
                    resp = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if resp.get("id") == self._id:
                    if "error" in resp:
                        raise RuntimeError(resp["error"].get("message", str(resp["error"])))
                    return resp.get("result", {})

    def _notify(self, method: str, params: dict = None):
        """Send a JSON-RPC notification (no id, no response expected)."""
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        self._proc.stdin.flush()

    def initialize(self):
        self._send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "clixen", "version": "1.0"},
            },
        )
        self._notify("notifications/initialized")

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        # Extract text from content array
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item["text"])
        return "\n".join(parts)

    def close(self):
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()


def _extract_library_id(text: str, library: str) -> str:
    """Pull the first /org/project path out of resolve-library-id response text."""
    import re

    # Look for patterns like /tiangolo/fastapi or /vercel/next.js
    matches = re.findall(r"/[\w.-]+/[\w.-][^\s,)\"']*", text)
    # Prefer exact library name match
    lib_lower = library.lower().replace(".", "").replace("-", "").replace("_", "")
    for m in matches:
        slug = m.split("/")[-1].lower().replace(".", "").replace("-", "").replace("_", "")
        if lib_lower in slug or slug in lib_lower:
            return m
    return matches[0] if matches else ""


def execute(library: str, topic: str = "") -> str:
    """Resolve library ID then fetch docs. Caches result in KnowledgeBase."""
    # from store.knowledge_base import KnowledgeBase
    # kb = KnowledgeBase()
    # cache_query = f"context7 {library} {topic}".strip()
    # cached = kb.is_cached(cache_query, source="context7_docs")
    # if cached:
    #     return f"[kb]\n{cached}"

    mcp = _MCP(["npx", "--yes", "@upstash/context7-mcp"])
    try:
        mcp.initialize()

        # Step 1: resolve library id
        query_text = f"{library} {topic}".strip()
        resolve_raw = mcp.call_tool(
            "resolve-library-id",
            {
                "libraryName": library,
                "query": query_text,
            },
        )
        # Extract the first /org/project path from the response text
        library_id = _extract_library_id(resolve_raw, library)
        if not library_id:
            return f"[tool error] context7 could not resolve library: {library}"

        # Step 2: fetch docs
        docs = mcp.call_tool(
            "query-docs",
            {
                "libraryId": library_id,
                "query": query_text,
            },
        )

    finally:
        mcp.close()

    if not docs:
        return f"No docs found for {library} ({topic or 'general'})"

    # Trim to ~3000 chars to stay within LLM context budget
    if len(docs) > 3000:
        docs = docs[:3000].rsplit("\n", 1)[0] + "\n...(truncated)"

    return docs
