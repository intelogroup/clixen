"""
MCP server — exposes every tool in tools/registry.py (denylist checks,
jsonschema validation, plan-mode blocking, confirmation gates, sanitize_output
all included) via the Model Context Protocol.

Previously this hand-wrote ~15 filesystem/shell wrappers calling straight into
tools/filesystem.py etc., bypassing execute_tool()'s whole guardrail chain —
the other ~250 registered tools (browser, calendar, gmail, ask_* orchestrator
tools) weren't reachable via MCP at all. Now every tool in ALL_TOOLS is
generated from the same schema harness.py already uses, routed through
execute_tool() for free.

Run:
    python mcp_server.py

Or add to ~/.claude/settings.json mcpServers:
    {
      "clixen": {
        "command": "<repo>/.venv/bin/python3",
        "args": ["<repo>/tools-harness/mcp_server.py"]
      }
    }
"""

import sys
from pathlib import Path

# Make tools importable
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from fastmcp import FastMCP
from fastmcp.tools.tool import Tool

from tools.registry import ALL_TOOLS, execute_tool

mcp = FastMCP("clixen")


class RegistryTool(Tool):
    """An MCP tool whose input schema and execution both come straight from
    tools/registry.py — no per-tool function to hand-write or keep in sync."""

    async def run(self, arguments: dict) -> object:
        result = execute_tool(self.name, arguments)
        return self.convert_result(result)


for _t in ALL_TOOLS:
    _fn = _t["function"]
    mcp.add_tool(RegistryTool(
        name=_fn["name"],
        description=_fn.get("description", ""),
        parameters=_fn.get("parameters", {"type": "object", "properties": {}}),
    ))


if __name__ == "__main__":
    mcp.run()
