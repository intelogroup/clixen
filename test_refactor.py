import sys
from pathlib import Path

# Add src to path for testing before installation
sys.path.append(str(Path(__file__).parent / "src"))

from g4l.orchestrator import run

def test_refactor():
    print("Testing refactored G4L architecture...")
    
    # 1. Test simple query
    print("\nQuery: What time is it?")
    response = run("What time is it?")
    print(f"Response: {response}")
    
    # 2. Test tool call (filesystem)
    print("\nQuery: List the files in the current directory.")
    response = run("List the files in the current directory.")
    print(f"Response: {response}")

if __name__ == "__main__":
    test_refactor()
