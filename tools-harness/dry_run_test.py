
import sys
from pathlib import Path

# Add the tools-harness directory to the path
sys.path.append(str(Path.cwd() / "tools-harness"))

from harness import parse_local_fs_action

def dry_run_test():
    test_queries = [
        "list files in ~/Downloads",
        "read file tools-harness/harness.py",
        "search for 'TODO' in tools-harness",
        "delete file temp.txt",
        "rename old.txt to new.txt",
        "what is the biggest file in src",
        "show tree of /Users/kalinovdameus/Developer/clixen",
    ]

    print("=== Dry-Run: Deterministic Tool Parsing Test ===")
    for query in test_queries:
        action = parse_local_fs_action(query)
        if action:
            print(f"Query: {query}")
            print(f"  -> Tool: {action.tool_name}")
            print(f"  -> Args: {action.arguments}")
        else:
            print(f"Query: {query}")
            print("  -> No deterministic action found (would fall back to LLM)")
    print("================================================")

if __name__ == "__main__":
    dry_run_test()
