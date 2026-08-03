# Qwen Web Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `qwen2.5-coder:7b` to use the `web_search` tool when the UI web-search toggle is on, instead of always being redirected to mistral-nemo.

**Architecture:** One guard condition in `harness.py` prevents qwen from ever getting `web_search`. Changing it to whitelist `qwen2.5-coder:7b` is sufficient — the LangGraph agent in `qwen_agent.py` already handles tool calling correctly, including streaming the final answer via `_pseudo_stream`. `qwen2.5-coder:1.5b` remains excluded (too small for synthesis quality). Mistral-nemo stays the primary `temporal`-intent model — this only affects the force_web_search toggle path.

**Tech Stack:** Python, existing qwen_agent.py (LangGraph), harness.py

---

## File Map

| Action | Path |
|--------|------|
| Modify | `tools-harness/harness.py` (1 line + comment) |
| Test | `tools-harness/test_harness_web_search.py` (new) |
| Sync | `~/developer/clixen/tools-harness/harness.py` |

---

## Task 1: Allow qwen2.5-coder:7b through the force_web_search gate

**Files:**
- Modify: `tools-harness/harness.py:200-206`
- Test: `tools-harness/test_harness_web_search.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/kalinovdameus/Developer/clixen/tools-harness/test_harness_web_search.py`:

```python
"""
Tests for force_web_search model selection in harness.py.

We test the routing logic directly — not the full LLM call — by inspecting
what model and tools harness.run() would select. We mock ollama_client.chat
to capture arguments and return a dummy response immediately.
"""
import pytest
from unittest.mock import patch, MagicMock


def _run_with_mocks(query: str, force_web_search: bool, model: str = None):
    """
    Call harness.run() with mocked LLM clients. Returns (model_used, tools_used).
    Captures the model + tools passed to ollama_client.chat or qwen_agent.run.
    """
    captured = {}

    def fake_chat(user_message, tools=None, model=DEFAULT_MODEL, **kwargs):
        captured["model"] = model
        captured["tools"] = [t["function"]["name"] for t in (tools or [])]
        return "dummy response"

    def fake_qwen_run(model, user_message, tool_schemas, **kwargs):
        captured["model"] = model
        captured["tools"] = [t["function"]["name"] for t in tool_schemas]
        return "dummy response"

    # Import here so patch targets are correct
    from clients.ollama_client import DEFAULT_MODEL

    with patch("clients.ollama_client.chat", side_effect=fake_chat), \
         patch("clients.qwen_agent.run", side_effect=fake_qwen_run):
        import harness
        harness.run(
            query=query,
            model=model,
            force_web_search=force_web_search,
        )

    return captured.get("model"), captured.get("tools", [])


def test_force_web_search_qwen7b_keeps_model_and_gets_web_search():
    """
    When force_web_search=True and the router chooses qwen2.5-coder:7b,
    the model should NOT be redirected to mistral-nemo.
    It should stay on qwen2.5-coder:7b and receive the web_search tool.
    """
    # A casual short query → router picks qwen2.5-coder:1.5b or 7b.
    # Use a medium-length casual query to land on qwen2.5-coder:7b.
    query = "tell me something interesting about the universe and space exploration trends"
    model, tools = _run_with_mocks(query, force_web_search=True)
    assert model == "qwen2.5-coder:7b", f"Expected qwen2.5-coder:7b, got {model}"
    assert "web_search" in tools, f"Expected web_search in tools, got {tools}"


def test_force_web_search_qwen1_5b_redirects_to_nemo():
    """
    qwen2.5-coder:1.5b is too small for web search synthesis.
    When force_web_search=True and the router chose 1.5b, it should
    be redirected to mistral-nemo.
    """
    # A very short casual query → router picks qwen2.5-coder:1.5b
    query = "hi"
    model, tools = _run_with_mocks(query, force_web_search=True)
    assert model == "mistral-nemo", f"Expected mistral-nemo (1.5b redirect), got {model}"
    assert "web_search" in tools


def test_force_web_search_gemma4_stays():
    """gemma4 should not be redirected — it's native tool-capable."""
    model, tools = _run_with_mocks("what is the weather", force_web_search=True, model="gemma4")
    assert model == "gemma4"
    assert "web_search" in tools


def test_force_web_search_nemo_stays():
    """mistral-nemo should not be redirected."""
    model, tools = _run_with_mocks("what is the weather", force_web_search=True, model="mistral-nemo")
    assert model == "mistral-nemo"
    assert "web_search" in tools


def test_no_force_web_search_qwen7b_gets_no_tools_for_casual():
    """Without force_web_search, casual qwen queries get no tools."""
    query = "tell me something interesting about the universe and space exploration trends"
    model, tools = _run_with_mocks(query, force_web_search=False)
    assert model == "qwen2.5-coder:7b"
    assert "web_search" not in tools
```

- [ ] **Step 2: Run to verify tests fail**

```bash
cd /Users/kalinovdameus/Developer/clixen/tools-harness
python3 -m pytest test_harness_web_search.py -v 2>&1 | tail -20
```

Expected: `test_force_web_search_qwen7b_keeps_model_and_gets_web_search` FAILS (model is mistral-nemo, not qwen2.5-coder:7b). Others may pass or fail — that's fine.

- [ ] **Step 3: Apply the fix to harness.py**

In `/Users/kalinovdameus/Developer/clixen/tools-harness/harness.py`, find this block (around line 200):

```python
    # UI web-search toggle: override whatever the router chose
    if force_web_search:
        active_tools = _tool("web_search")
        intent = "web_search"
        # Ensure a tool-native model handles the search; qwen streaming doesn't work end-to-end
        if routed_model not in ("gemma4", "mistral-nemo", "mistral"):
            routed_model = "mistral-nemo"
```

Replace with:

```python
    # UI web-search toggle: override whatever the router chose
    if force_web_search:
        active_tools = _tool("web_search")
        intent = "web_search"
        # qwen2.5-coder:7b handles web_search via LangGraph (qwen_agent.py); 1.5b too small → nemo
        if routed_model not in ("gemma4", "mistral-nemo", "mistral", "qwen2.5-coder:7b"):
            routed_model = "mistral-nemo"
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd /Users/kalinovdameus/Developer/clixen/tools-harness
python3 -m pytest test_harness_web_search.py -v
```

Expected:
```
test_harness_web_search.py::test_force_web_search_qwen7b_keeps_model_and_gets_web_search PASSED
test_harness_web_search.py::test_force_web_search_qwen1_5b_redirects_to_nemo PASSED
test_harness_web_search.py::test_force_web_search_gemma4_stays PASSED
test_harness_web_search.py::test_force_web_search_nemo_stays PASSED
test_harness_web_search.py::test_no_force_web_search_qwen7b_gets_no_tools_for_casual PASSED
5 passed
```

- [ ] **Step 5: Sync to lowercase repo and commit**

```bash
cp ~/Developer/clixen/tools-harness/harness.py \
   ~/developer/clixen/tools-harness/harness.py

diff ~/Developer/clixen/tools-harness/harness.py \
     ~/developer/clixen/tools-harness/harness.py
# Expected: no output
```
