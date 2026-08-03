"""
Phase 1: Tool-Level Verification
Tests which tools are available in standard chat mode (non-IDE).
"""
import sys
import os
import json
from pathlib import Path

# Add tools-harness to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools-harness"))

from tools.registry import ALL_TOOLS, EXECUTORS

# Get all tool names
all_tool_names = [t["function"]["name"] for t in ALL_TOOLS]

print("=" * 60)
print("PHASE 1.0: Tool Registry Audit")
print("=" * 60)

# Check which tools are available
critical_tools = {
    "write_file": "Write text to file",
    "text_to_file": "Create text/HTML file from content",
    "data_to_xlsx": "Create Excel from JSON/CSV",
    "bash_exec": "Execute shell commands",
    "web_search": "Web search via search graph",
    "markdown_to_docx": "Create Word doc from markdown",
    "markdown_to_pdf": "Create PDF from markdown",
    "read_file": "Read file content",
    "list_directory": "List directory contents",
}

print("\nTool availability in ALL_TOOLS:")
for tool, desc in critical_tools.items():
    available = tool in all_tool_names
    print(f"  {'✅' if available else '❌'} {tool:25s} - {desc}")

# Check executor mapping
print("\nExecutor mapping:")
for tool in critical_tools:
    has_executor = tool in EXECUTORS
    print(f"  {'✅' if has_executor else '❌'} {tool:25s} - executor {'exists' if has_executor else 'MISSING'}")

# Now test harness routing to see which tools get activated
print("\n" + "=" * 60)
print("PHASE 1.1: write_file availability in chat mode")
print("=" * 60)

from harness import run

# Test 1.1: Write file in standard chat mode
result = run(
    "write hello world to /tmp/test_g4l_phase1.txt",
    chat_id="test_write_phase1",
    model="gemma4"
)
print(f"\nResult: {result[:500]}")
print(f"Full result length: {len(result)} chars")

# Check if file was created
test_file = Path("/tmp/test_g4l_phase1.txt")
if test_file.exists():
    print(f"✅ File created! Content: {test_file.read_text()[:100]}")
else:
    print("❌ File NOT created")

# Test 1.4: bash_exec in chat mode
print("\n" + "=" * 60)
print("PHASE 1.4: bash_exec availability in chat mode")
print("=" * 60)

result = run(
    "run this command: echo 'hello from bash' > /tmp/test_bash_phase1.txt",
    chat_id="test_bash_phase1",
    model="gemma4"
)
print(f"\nResult: {result[:500]}")

test_bash_file = Path("/tmp/test_bash_phase1.txt")
if test_bash_file.exists():
    print(f"✅ File created via bash! Content: {test_bash_file.read_text()[:100]}")
else:
    print("❌ File NOT created via bash")

print("\nPhase 1.1 and 1.4 complete.")
