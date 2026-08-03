# LangGraph Agent Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a LangGraph orchestration layer above the existing harness so agents can run multi-turn setup wizards, route tool tasks, answer directly, and say "I can't" for unsupported requests.

**Architecture:** A `StateGraph` with five nodes (router, tool_task, direct_answer, cant_do, wizard) sits above `harness.run()`. The wizard node uses `SqliteSaver` to persist credential-collection state across turns per `thread_id`. All existing jobs, tools, vault, and ollama_client are called from graph nodes without modification. A new `/agent/stream` SSE endpoint in `chat_ui.py` exposes the graph to the UI.

**Tech Stack:** Python 3.12, LangGraph ≥0.2.0, langgraph-checkpoint ≥1.0.0, SQLite (existing jobs/jobs.db dir), macOS Keychain via keyring (existing vault.py), Ollama (existing ollama_client.py)

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `tools-harness/agents/__init__.py` | package marker |
| Create | `tools-harness/agents/state.py` | `AgentState` TypedDict |
| Create | `tools-harness/agents/capability_map.py` | `CANT_DO_RE`, `WIZARD_TRIGGER_RE`, capability string |
| Create | `tools-harness/agents/nodes/__init__.py` | package marker |
| Create | `tools-harness/agents/nodes/cant_do_node.py` | static capability list response |
| Create | `tools-harness/agents/nodes/router_node.py` | classify() + wizard/cant-do detection |
| Create | `tools-harness/agents/nodes/direct_answer_node.py` | ollama_client.chat(tools=[]) |
| Create | `tools-harness/agents/nodes/tool_task_node.py` | harness.run() wrapper |
| Create | `tools-harness/agents/nodes/wizard/__init__.py` | package marker |
| Create | `tools-harness/agents/nodes/wizard/workflows.py` | `WORKFLOW_MANIFESTS` per workflow |
| Create | `tools-harness/agents/nodes/wizard/register.py` | `_register_workflow()` — launchd plist + job_queue |
| Create | `tools-harness/agents/nodes/wizard/wizard_node.py` | credential collection state machine |
| Create | `tools-harness/agents/graph.py` | `build_graph()`, `get_agent()` singleton, `run_agent()` |
| Modify | `tools-harness/chat_ui.py` | add `GET /agent/stream` SSE endpoint |
| Create | `tools-harness/tests/test_agent_router.py` | router node tests |
| Create | `tools-harness/tests/test_agent_wizard.py` | wizard node tests |
| Create | `tools-harness/tests/test_agent_cant_do.py` | cant_do node tests |
| Create | `tools-harness/tests/test_agent_graph.py` | end-to-end graph smoke tests |

---

## Task 1: Install LangGraph

**Files:**
- Run: `pip install`

- [ ] **Step 1: Install packages**

```bash
cd /Users/kalinovdameus/Developer/clixen/tools-harness
pip install "langgraph>=0.2.0" "langgraph-checkpoint>=1.0.0"
```

- [ ] **Step 2: Verify install**

```bash
python -c "import langgraph; from langgraph.checkpoint.sqlite import SqliteSaver; print('ok')"
```

Expected: `ok`

---

## Task 2: AgentState TypedDict

**Files:**
- Create: `tools-harness/agents/__init__.py`
- Create: `tools-harness/agents/state.py`

- [ ] **Step 1: Write failing test**

Create `tools-harness/tests/test_agent_state.py`:

```python
from agents.state import AgentState

def test_agent_state_has_required_fields():
    s = AgentState(
        messages=[],
        thread_id="t1",
        route="",
        intent="",
        routed_model="",
        tool_result="",
        tool_error=None,
        direct_answer="",
        wizard_active=False,
        wizard_workflow="",
        wizard_collected={},
        wizard_missing=[],
        wizard_step="",
        cant_do_reason="",
    )
    assert s["thread_id"] == "t1"
    assert s["wizard_active"] is False
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd tools-harness && python -m pytest tests/test_agent_state.py -v
```

Expected: `ModuleNotFoundError: No module named 'agents'`

- [ ] **Step 3: Create package + state**

`tools-harness/agents/__init__.py` — empty file.

`tools-harness/agents/state.py`:

```python
from typing import Annotated, Literal
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # Core conversation
    messages:         Annotated[list, add_messages]
    thread_id:        str

    # Routing
    route:            Literal["wizard", "tool_task", "direct_answer", "cant_do", ""]
    intent:           str
    routed_model:     str

    # Tool task
    tool_result:      str
    tool_error:       str | None

    # Direct answer
    direct_answer:    str

    # Wizard
    wizard_active:    bool
    wizard_workflow:  str
    wizard_collected: dict[str, str]
    wizard_missing:   list[str]
    wizard_step:      str

    # Capability guard
    cant_do_reason:   str
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_agent_state.py -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add agents/__init__.py agents/state.py tests/test_agent_state.py
git commit -m "feat(agents): add AgentState TypedDict"
```

---

## Task 3: Capability Map

**Files:**
- Create: `tools-harness/agents/capability_map.py`

- [ ] **Step 1: Write failing tests**

Create `tools-harness/tests/test_agent_cant_do.py`:

```python
import re
from agents.capability_map import CANT_DO_RE, WIZARD_TRIGGER_RE, CAPABILITY_STRING

def test_cant_do_matches_phone_call():
    assert CANT_DO_RE.search("call me on my phone")

def test_cant_do_matches_payment():
    assert CANT_DO_RE.search("pay with stripe")

def test_cant_do_matches_social_post():
    assert CANT_DO_RE.search("post to instagram")

def test_cant_do_no_match_on_email():
    assert not CANT_DO_RE.search("send me an email summary")

def test_wizard_trigger_morning_briefing():
    assert WIZARD_TRIGGER_RE.search("set up morning briefing")

def test_wizard_trigger_inbox_monitor():
    assert WIZARD_TRIGGER_RE.search("configure inbox monitor")

def test_wizard_trigger_example_workflow():
    assert WIZARD_TRIGGER_RE.search("enable example_workflow")

def test_wizard_no_trigger_on_casual():
    assert not WIZARD_TRIGGER_RE.search("how are you")

def test_capability_string_not_empty():
    assert len(CAPABILITY_STRING) > 50
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_agent_cant_do.py -v
```

Expected: `ModuleNotFoundError: No module named 'agents.capability_map'`

- [ ] **Step 3: Create capability_map.py**

`tools-harness/agents/capability_map.py`:

```python
import re

CANT_DO_RE = re.compile(
    r"\b(call (me|my phone|us)|make a (phone|voice) call|send (an? )?sms"
    r"|pay|purchase|buy (with|using)|stripe|venmo|paypal"
    r"|post (to|on) (twitter|instagram|tiktok|linkedin|facebook)"
    r"|dropbox|google drive|icloud|onedrive)\b",
    re.IGNORECASE,
)

WIZARD_TRIGGER_RE = re.compile(
    r"\b(set\s*up|setup|configure|enable|install|register|activate)\b.{0,50}"
    r"\b(morning[\s\-]?briefing|inbox[\s\-]?monitor|example_workflow"
    r"|workflow|credential|bot[\s\-]?token|telegram)\b",
    re.IGNORECASE,
)

# Intents that go to direct_answer (no tool loop needed)
DIRECT_ANSWER_INTENTS = {"casual", "analysis", "code_heavy", "code_medium", "code_quick", "math", "multilingual"}

CAPABILITY_STRING = """I can't do that. Here's what I can help with:
• Email — read inbox, send, summarize (Gmail via OAuth)
• Calendar — list / create / delete events (Google Calendar)
• Tasks — manage todo lists (Google Tasks)
• Web search — news, prices, temporal queries (Tavily / Brave / Exa / SerpAPI)
• Browser automation — navigate, click, fill forms (Playwright)
• Code — write, debug, execute Python and bash
• Files — read / write / search local filesystem
• Git — status, diff, commit, branch
• Workflows — morning briefing, inbox monitor, example_workflow (after setup)
• Messaging — send Telegram notifications"""
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_agent_cant_do.py -v
```

Expected: all `PASSED`

- [ ] **Step 5: Commit**

```bash
git add agents/capability_map.py tests/test_agent_cant_do.py
git commit -m "feat(agents): add capability map and regex patterns"
```

---

## Task 4: cant_do_node and router_node

**Files:**
- Create: `tools-harness/agents/nodes/__init__.py`
- Create: `tools-harness/agents/nodes/cant_do_node.py`
- Create: `tools-harness/agents/nodes/router_node.py`
- Create: `tools-harness/tests/test_agent_router.py`

- [ ] **Step 1: Write failing tests**

Create `tools-harness/tests/test_agent_router.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from unittest.mock import patch
from langchain_core.messages import HumanMessage
from agents.state import AgentState
from agents.nodes.router_node import router_node
from agents.nodes.cant_do_node import cant_do_node


def _state(msg, **kwargs):
    base = AgentState(
        messages=[HumanMessage(content=msg)],
        thread_id="t1", route="", intent="", routed_model="",
        tool_result="", tool_error=None, direct_answer="",
        wizard_active=False, wizard_workflow="", wizard_collected={},
        wizard_missing=[], wizard_step="", cant_do_reason="",
    )
    base.update(kwargs)
    return base


def test_router_cant_do_phone():
    with patch("agents.nodes.router_node.classify", return_value=("qwen3:4b", "casual")):
        result = router_node(_state("call me on my phone"))
    assert result["route"] == "cant_do"


def test_router_wizard_morning_briefing():
    with patch("agents.nodes.router_node.classify", return_value=("qwen3:4b", "casual")):
        result = router_node(_state("set up morning briefing"))
    assert result["route"] == "wizard"
    assert result["wizard_workflow"] == "morning_briefing"


def test_router_direct_answer_casual():
    with patch("agents.nodes.router_node.classify", return_value=("qwen3:1.7b", "casual")):
        result = router_node(_state("how are you?"))
    assert result["route"] == "direct_answer"


def test_router_tool_task_email():
    with patch("agents.nodes.router_node.classify", return_value=("mistral-nemo", "email")):
        result = router_node(_state("check my emails"))
    assert result["route"] == "tool_task"
    assert result["intent"] == "email"


def test_router_wizard_stays_active_mid_session():
    """Active wizard keeps routing to wizard regardless of message content."""
    with patch("agents.nodes.router_node.classify", return_value=("qwen3:4b", "casual")):
        result = router_node(_state("how are you?", wizard_active=True, wizard_workflow="example_workflow"))
    assert result["route"] == "wizard"


def test_cant_do_node_returns_capability_string():
    result = cant_do_node(_state("pay for something"))
    assert "can't" in result["cant_do_reason"].lower()
    assert "Email" in result["cant_do_reason"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_agent_router.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create nodes package + cant_do_node**

`tools-harness/agents/nodes/__init__.py` — empty file.

`tools-harness/agents/nodes/cant_do_node.py`:

```python
from agents.state import AgentState
from agents.capability_map import CAPABILITY_STRING
from langchain_core.messages import AIMessage


def cant_do_node(state: AgentState) -> AgentState:
    return {**state, "cant_do_reason": CAPABILITY_STRING}
```

- [ ] **Step 4: Create router_node**

`tools-harness/agents/nodes/router_node.py`:

```python
import re
from langchain_core.messages import AIMessage
from agents.state import AgentState
from agents.capability_map import CANT_DO_RE, WIZARD_TRIGGER_RE, DIRECT_ANSWER_INTENTS
from clients.router import classify

_WORKFLOW_NAMES = {
    "morning_briefing": re.compile(r"morning[\s\-]?briefing", re.IGNORECASE),
    "inbox_monitor":    re.compile(r"inbox[\s\-]?monitor", re.IGNORECASE),
    "example_workflow":      re.compile(r"example_workflow", re.IGNORECASE),
}


def router_node(state: AgentState) -> AgentState:
    # Active wizard owns all turns until it sets wizard_active=False
    if state.get("wizard_active"):
        return {**state, "route": "wizard"}

    query = state["messages"][-1].content

    # Cant-do check first (highest priority after wizard)
    if CANT_DO_RE.search(query):
        return {**state, "route": "cant_do"}

    # Setup wizard trigger
    if WIZARD_TRIGGER_RE.search(query):
        workflow = next(
            (name for name, pat in _WORKFLOW_NAMES.items() if pat.search(query)),
            "morning_briefing",  # default if only "workflow" / generic term matched
        )
        return {**state, "route": "wizard", "wizard_workflow": workflow,
                "wizard_active": True, "wizard_step": "init"}

    # Intent classification
    model, intent = classify(query)
    route = "direct_answer" if intent in DIRECT_ANSWER_INTENTS else "tool_task"
    return {**state, "route": route, "intent": intent, "routed_model": model}
```

- [ ] **Step 5: Run tests — expect pass**

```bash
python -m pytest tests/test_agent_router.py -v
```

Expected: all `PASSED`

- [ ] **Step 6: Commit**

```bash
git add agents/nodes/__init__.py agents/nodes/cant_do_node.py agents/nodes/router_node.py tests/test_agent_router.py
git commit -m "feat(agents): add router_node and cant_do_node"
```

---

## Task 5: direct_answer_node and tool_task_node

**Files:**
- Create: `tools-harness/agents/nodes/direct_answer_node.py`
- Create: `tools-harness/agents/nodes/tool_task_node.py`

- [ ] **Step 1: Write failing tests**

Add to `tools-harness/tests/test_agent_router.py`:

```python
from unittest.mock import patch, MagicMock
from agents.nodes.direct_answer_node import direct_answer_node
from agents.nodes.tool_task_node import tool_task_node


def test_direct_answer_node_calls_ollama():
    with patch("agents.nodes.direct_answer_node.chat", return_value="42") as mock_chat:
        result = direct_answer_node(_state("what is 2+2?", routed_model="qwen3:4b"))
    mock_chat.assert_called_once()
    assert result["direct_answer"] == "42"


def test_tool_task_node_calls_harness():
    with patch("agents.nodes.tool_task_node.harness_run", return_value="got emails") as mock_run:
        result = tool_task_node(_state("check my emails", routed_model="mistral-nemo"))
    mock_run.assert_called_once()
    assert result["tool_result"] == "got emails"


def test_tool_task_node_captures_error():
    with patch("agents.nodes.tool_task_node.harness_run", side_effect=Exception("boom")):
        result = tool_task_node(_state("check my emails"))
    assert result["tool_error"] == "boom"
    assert result["tool_result"] == ""
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_agent_router.py -v -k "direct_answer or tool_task"
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create direct_answer_node**

`tools-harness/agents/nodes/direct_answer_node.py`:

```python
from agents.state import AgentState
from clients.ollama_client import chat


def direct_answer_node(state: AgentState) -> AgentState:
    query = state["messages"][-1].content
    model = state.get("routed_model") or "qwen3:4b"
    token_cb = state.get("_token_cb")
    reply = chat(
        user_message=query,
        tools=[],
        model=model,
        on_token=token_cb,
    )
    return {**state, "direct_answer": reply}
```

- [ ] **Step 4: Create tool_task_node**

`tools-harness/agents/nodes/tool_task_node.py`:

```python
from agents.state import AgentState
import harness as _harness


def harness_run(query: str, chat_id: str, on_token=None) -> str:
    """Thin wrapper so tests can patch at module level."""
    return _harness.run(query=query, chat_id=chat_id, on_token=on_token)


def tool_task_node(state: AgentState) -> AgentState:
    query = state["messages"][-1].content
    thread_id = state.get("thread_id", "agent_default")
    token_cb = state.get("_token_cb")
    try:
        result = harness_run(query, chat_id=thread_id, on_token=token_cb)
        return {**state, "tool_result": result, "tool_error": None}
    except Exception as exc:
        return {**state, "tool_result": "", "tool_error": str(exc)}
```

- [ ] **Step 5: Run tests — expect pass**

```bash
python -m pytest tests/test_agent_router.py -v
```

Expected: all `PASSED`

- [ ] **Step 6: Commit**

```bash
git add agents/nodes/direct_answer_node.py agents/nodes/tool_task_node.py tests/test_agent_router.py
git commit -m "feat(agents): add direct_answer_node and tool_task_node"
```

---

## Task 6: Workflow Manifests

**Files:**
- Create: `tools-harness/agents/nodes/wizard/__init__.py`
- Create: `tools-harness/agents/nodes/wizard/workflows.py`

- [ ] **Step 1: Write failing tests**

Create `tools-harness/tests/test_agent_wizard.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from agents.nodes.wizard.workflows import WORKFLOW_MANIFESTS


def test_all_three_workflows_defined():
    assert "morning_briefing" in WORKFLOW_MANIFESTS
    assert "inbox_monitor" in WORKFLOW_MANIFESTS
    assert "example_workflow" in WORKFLOW_MANIFESTS


def test_each_manifest_has_credentials_list():
    for name, manifest in WORKFLOW_MANIFESTS.items():
        assert "credentials" in manifest, f"{name} missing credentials"
        assert len(manifest["credentials"]) > 0


def test_each_credential_has_required_fields():
    for name, manifest in WORKFLOW_MANIFESTS.items():
        for cred in manifest["credentials"]:
            assert "key" in cred, f"{name} cred missing key"
            assert "prompt" in cred, f"{name} cred missing prompt"
            assert "validate" in cred, f"{name} cred missing validate"


def test_morning_briefing_requires_telegram_and_google():
    keys = [c["key"] for c in WORKFLOW_MANIFESTS["morning_briefing"]["credentials"]]
    assert "TELEGRAM_BOT_TOKEN" in keys
    assert "TELEGRAM_OWNER_CHAT_ID" in keys
    assert "GOOGLE_CREDENTIALS_PATH" in keys


def test_example_workflow_validate_accepts_valid_email():
    creds = {c["key"]: c for c in WORKFLOW_MANIFESTS["example_workflow"]["credentials"]}
    assert creds["EXAMPLE_WORKFLOW_EMAIL"]["validate"]("user@example.com")
    assert not creds["EXAMPLE_WORKFLOW_EMAIL"]["validate"]("notanemail")


def test_example_workflow_validate_rejects_short_password():
    creds = {c["key"]: c for c in WORKFLOW_MANIFESTS["example_workflow"]["credentials"]}
    assert not creds["EXAMPLE_WORKFLOW_PASSWORD"]["validate"]("abc")
    assert creds["EXAMPLE_WORKFLOW_PASSWORD"]["validate"]("abcd")
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_agent_wizard.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create wizard package + workflows.py**

`tools-harness/agents/nodes/wizard/__init__.py` — empty file.

`tools-harness/agents/nodes/wizard/workflows.py`:

```python
from pathlib import Path

WORKFLOW_MANIFESTS: dict[str, dict] = {
    "morning_briefing": {
        "description": "Daily Telegram digest with calendar, email, and tasks",
        "credentials": [
            {
                "key": "TELEGRAM_BOT_TOKEN",
                "prompt": "Paste your Telegram Bot Token (from @BotFather):",
                "hint": "Looks like 123456789:ABCdef...",
                "validate": lambda v: ":" in v and len(v) > 30,
            },
            {
                "key": "TELEGRAM_OWNER_CHAT_ID",
                "prompt": "Paste your Telegram Chat ID (send /start to your bot, then open https://api.telegram.org/bot<TOKEN>/getUpdates):",
                "hint": "A plain number like 123456789",
                "validate": lambda v: v.strip().lstrip("-").isdigit(),
            },
            {
                "key": "GOOGLE_CREDENTIALS_PATH",
                "prompt": "Paste the full path to your Google OAuth credentials.json file:",
                "hint": "e.g. /Users/you/Downloads/credentials.json",
                "validate": lambda v: Path(v.strip()).exists(),
            },
            {
                "key": "BRIEFING_HOUR",
                "prompt": "At what hour (0–23) should the briefing run? (default: 8)",
                "hint": "Enter a number 0–23",
                "validate": lambda v: v.strip().isdigit() and 0 <= int(v.strip()) <= 23,
                "default": "8",
            },
        ],
    },
    "inbox_monitor": {
        "description": "Gmail attachment watcher — PDF / Excel / Word → Telegram alerts",
        "credentials": [
            {
                "key": "TELEGRAM_BOT_TOKEN",
                "prompt": "Paste your Telegram Bot Token (from @BotFather):",
                "hint": "Looks like 123456789:ABCdef...",
                "validate": lambda v: ":" in v and len(v) > 30,
            },
            {
                "key": "TELEGRAM_OWNER_CHAT_ID",
                "prompt": "Paste your Telegram Chat ID:",
                "hint": "A plain number like 123456789",
                "validate": lambda v: v.strip().lstrip("-").isdigit(),
            },
            {
                "key": "GOOGLE_CREDENTIALS_PATH",
                "prompt": "Paste the full path to your Google OAuth credentials.json file:",
                "hint": "e.g. /Users/you/Downloads/credentials.json",
                "validate": lambda v: Path(v.strip()).exists(),
            },
            {
                "key": "WATCHED_SENDERS",
                "prompt": "Comma-separated email addresses to watch for attachments:",
                "hint": "e.g. boss@company.com, partner@example.com",
                "validate": lambda v: "@" in v,
            },
        ],
    },
    "example_workflow": {
        "description": "Example third-party site automation with stored credentials",
        "credentials": [
            {
                "key": "EXAMPLE_WORKFLOW_EMAIL",
                "prompt": "Your Example Workflow account email:",
                "validate": lambda v: "@" in v and len(v) > 5,
            },
            {
                "key": "EXAMPLE_WORKFLOW_PASSWORD",
                "prompt": "Your Example Workflow account password (stored in macOS Keychain):",
                "validate": lambda v: len(v) >= 4,
            },
        ],
    },
}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_agent_wizard.py -v
```

Expected: all `PASSED`

- [ ] **Step 5: Commit**

```bash
git add agents/nodes/wizard/__init__.py agents/nodes/wizard/workflows.py tests/test_agent_wizard.py
git commit -m "feat(agents): add workflow manifests for setup wizard"
```

---

## Task 7: register.py + wizard_node

**Files:**
- Create: `tools-harness/agents/nodes/wizard/register.py`
- Create: `tools-harness/agents/nodes/wizard/wizard_node.py`

- [ ] **Step 1: Write failing tests**

Add to `tools-harness/tests/test_agent_wizard.py`:

```python
from unittest.mock import patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage
from agents.state import AgentState
from agents.nodes.wizard.wizard_node import wizard_node


def _wstate(msg, workflow, step, collected=None, missing=None, active=True):
    return AgentState(
        messages=[HumanMessage(content=msg)],
        thread_id="t1", route="wizard", intent="", routed_model="",
        tool_result="", tool_error=None, direct_answer="",
        wizard_active=active,
        wizard_workflow=workflow,
        wizard_collected=collected or {},
        wizard_missing=missing or [],
        wizard_step=step,
        cant_do_reason="",
    )


def test_wizard_init_asks_for_first_missing_cred():
    with patch("agents.nodes.wizard.wizard_node.vault") as mock_vault:
        mock_vault.has.return_value = False
        result = wizard_node(_wstate("set up example_workflow", "example_workflow", "init"))
    last_msg = result["messages"][-1]
    assert isinstance(last_msg, AIMessage)
    assert "email" in last_msg.content.lower() or "example_workflow" in last_msg.content.lower()
    assert result["wizard_step"] == "EXAMPLE_WORKFLOW_EMAIL"


def test_wizard_invalid_value_re_asks():
    with patch("agents.nodes.wizard.wizard_node.vault") as mock_vault:
        mock_vault.has.return_value = False
        result = wizard_node(_wstate("notanemail", "example_workflow", "EXAMPLE_WORKFLOW_EMAIL",
                                     missing=["EXAMPLE_WORKFLOW_EMAIL", "EXAMPLE_WORKFLOW_PASSWORD"]))
    assert result["wizard_step"] == "EXAMPLE_WORKFLOW_EMAIL"
    assert result["wizard_active"] is True


def test_wizard_valid_value_advances_step():
    with patch("agents.nodes.wizard.wizard_node.vault") as mock_vault, \
         patch("agents.nodes.wizard.wizard_node._register_workflow"):
        mock_vault.has.return_value = False
        result = wizard_node(_wstate("user@example.com", "example_workflow", "EXAMPLE_WORKFLOW_EMAIL",
                                     missing=["EXAMPLE_WORKFLOW_EMAIL", "EXAMPLE_WORKFLOW_PASSWORD"]))
    assert result["wizard_step"] == "EXAMPLE_WORKFLOW_PASSWORD"
    assert "EXAMPLE_WORKFLOW_EMAIL" in result["wizard_collected"]


def test_wizard_completes_when_all_creds_collected():
    with patch("agents.nodes.wizard.wizard_node.vault") as mock_vault, \
         patch("agents.nodes.wizard.wizard_node._register_workflow") as mock_reg:
        mock_vault.has.return_value = False
        result = wizard_node(_wstate("mypassword", "example_workflow", "EXAMPLE_WORKFLOW_PASSWORD",
                                     collected={"EXAMPLE_WORKFLOW_EMAIL": "u@x.com"},
                                     missing=["EXAMPLE_WORKFLOW_PASSWORD"]))
    mock_reg.assert_called_once_with("example_workflow")
    assert result["wizard_active"] is False


def test_wizard_skips_creds_already_in_vault():
    with patch("agents.nodes.wizard.wizard_node.vault") as mock_vault, \
         patch("agents.nodes.wizard.wizard_node._register_workflow") as mock_reg:
        mock_vault.has.side_effect = lambda k: k == "EXAMPLE_WORKFLOW_EMAIL"
        result = wizard_node(_wstate("set up example_workflow", "example_workflow", "init"))
    # Should ask for password (email already in vault), not email
    assert result["wizard_step"] == "EXAMPLE_WORKFLOW_PASSWORD"
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_agent_wizard.py -v -k "wizard_init or wizard_invalid or wizard_valid or wizard_completes or wizard_skips"
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create register.py**

`tools-harness/agents/nodes/wizard/register.py`:

```python
import os
from pathlib import Path


def _write_morning_briefing_plist(hour: str) -> None:
    plist_path = Path.home() / "Library/LaunchAgents/com.clixen.morningbriefing.plist"
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.clixen.morningbriefing</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/{os.environ.get('USER','user')}/miniforge3/bin/python3</string>
        <string>-m</string><string>jobs.morning_briefing_job</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{Path(__file__).parent.parent.parent.parent}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key><string>1</string>
        <key>BRIEFING_LLM_SUMMARY</key><string>1</string>
        <key>BRIEFING_MODEL</key><string>qwen3:4b</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>0</integer></dict>
    <key>StandardOutPath</key>
    <string>{Path(__file__).parent.parent.parent.parent}/morning_briefing.log</string>
    <key>StandardErrorPath</key>
    <string>{Path(__file__).parent.parent.parent.parent}/morning_briefing.err.log</string>
    <key>KeepAlive</key><false/>
</dict>
</plist>"""
    plist_path.write_text(plist_content)


def _register_workflow(workflow: str) -> None:
    """Register workflow: write launchd plist (scheduled) or enqueue (on-demand)."""
    from tools.vault import vault
    from jobs import job_queue

    if workflow == "morning_briefing":
        hour = vault.get("BRIEFING_HOUR") or "8"
        _write_morning_briefing_plist(hour)

    elif workflow == "inbox_monitor":
        job_queue.init()
        job_queue.enqueue("email_pipeline", {})

    # example_workflow: on-demand only — credentials stored, no schedule needed
```

- [ ] **Step 4: Create wizard_node.py**

`tools-harness/agents/nodes/wizard/wizard_node.py`:

```python
from langchain_core.messages import AIMessage
from agents.state import AgentState
from agents.nodes.wizard.workflows import WORKFLOW_MANIFESTS
from agents.nodes.wizard.register import _register_workflow
from tools.vault import vault


def wizard_node(state: AgentState) -> AgentState:
    workflow = state["wizard_workflow"]
    manifest = WORKFLOW_MANIFESTS[workflow]
    creds = manifest["credentials"]
    collected = dict(state["wizard_collected"])
    step = state["wizard_step"]
    msg = state["messages"][-1].content.strip()

    # ── Phase 1: init — compute missing keys ──────────────────────────
    if step == "init":
        missing = [c["key"] for c in creds if not vault.has(c["key"]) and c["key"] not in collected]
        if not missing:
            _register_workflow(workflow)
            return {**state, "wizard_active": False, "wizard_missing": [],
                    "messages": [*state["messages"], AIMessage(
                        f"All credentials are already set. {workflow} is active!"
                    )]}
        first = missing[0]
        cred_def = next(c for c in creds if c["key"] == first)
        prompt = cred_def["prompt"]
        if hint := cred_def.get("hint"):
            prompt += f"\n({hint})"
        return {**state, "wizard_missing": missing, "wizard_step": first,
                "messages": [*state["messages"], AIMessage(prompt)]}

    # ── Phase 2: receiving credential value ───────────────────────────
    current_key = step
    cred_def = next(c for c in creds if c["key"] == current_key)

    if not cred_def["validate"](msg):
        hint = cred_def.get("hint", "")
        return {**state, "messages": [*state["messages"], AIMessage(
            f"That doesn't look right.{' ' + hint if hint else ''} Try again:"
        )]}

    vault.set(current_key, msg)
    collected[current_key] = msg

    missing = [k for k in state["wizard_missing"] if k not in collected and not vault.has(k)]
    if missing:
        next_key = missing[0]
        next_cred = next(c for c in creds if c["key"] == next_key)
        prompt = next_cred["prompt"]
        if hint := next_cred.get("hint"):
            prompt += f"\n({hint})"
        return {**state, "wizard_collected": collected, "wizard_missing": missing,
                "wizard_step": next_key,
                "messages": [*state["messages"], AIMessage(prompt)]}

    _register_workflow(workflow)
    return {**state, "wizard_active": False, "wizard_collected": collected, "wizard_missing": [],
            "messages": [*state["messages"], AIMessage(
                f"All credentials saved to Keychain. {workflow} workflow is now active!"
            )]}
```

- [ ] **Step 5: Run tests — expect pass**

```bash
python -m pytest tests/test_agent_wizard.py -v
```

Expected: all `PASSED`

- [ ] **Step 6: Commit**

```bash
git add agents/nodes/wizard/register.py agents/nodes/wizard/wizard_node.py tests/test_agent_wizard.py
git commit -m "feat(agents): add wizard_node and workflow registration"
```

---

## Task 8: Main Graph

**Files:**
- Create: `tools-harness/agents/graph.py`
- Create: `tools-harness/tests/test_agent_graph.py`

- [ ] **Step 1: Write failing smoke tests**

Create `tools-harness/tests/test_agent_graph.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from unittest.mock import patch
from agents.graph import run_agent


def test_run_agent_direct_answer():
    with patch("agents.nodes.router_node.classify", return_value=("qwen3:4b", "casual")), \
         patch("agents.nodes.direct_answer_node.chat", return_value="I'm fine!"):
        reply, route, intent = run_agent("how are you?", thread_id="smoke1")
    assert route == "direct_answer"
    assert reply == "I'm fine!"


def test_run_agent_cant_do():
    with patch("agents.nodes.router_node.classify", return_value=("qwen3:4b", "casual")):
        reply, route, intent = run_agent("call me on my phone", thread_id="smoke2")
    assert route == "cant_do"
    assert "can't" in reply.lower()


def test_run_agent_tool_task():
    with patch("agents.nodes.router_node.classify", return_value=("mistral-nemo", "email")), \
         patch("agents.nodes.tool_task_node.harness_run", return_value="3 new emails"):
        reply, route, intent = run_agent("check my emails", thread_id="smoke3")
    assert route == "tool_task"
    assert reply == "3 new emails"


def test_run_agent_wizard_starts():
    with patch("agents.nodes.router_node.classify", return_value=("qwen3:4b", "casual")), \
         patch("agents.nodes.wizard.wizard_node.vault") as mock_vault:
        mock_vault.has.return_value = False
        reply, route, intent = run_agent("set up morning briefing", thread_id="smoke4")
    assert route == "wizard"
    assert "TELEGRAM" in reply or "telegram" in reply.lower() or "Bot Token" in reply
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_agent_graph.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create graph.py**

`tools-harness/agents/graph.py`:

```python
from __future__ import annotations
from pathlib import Path
from typing import Callable
from functools import lru_cache

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from agents.state import AgentState
from agents.nodes.router_node import router_node
from agents.nodes.tool_task_node import tool_task_node
from agents.nodes.direct_answer_node import direct_answer_node
from agents.nodes.cant_do_node import cant_do_node
from agents.nodes.wizard.wizard_node import wizard_node

_DB_PATH = str(Path(__file__).parent.parent / "jobs" / "langgraph.db")


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("router",        router_node)
    g.add_node("tool_task",     tool_task_node)
    g.add_node("direct_answer", direct_answer_node)
    g.add_node("cant_do",       cant_do_node)
    g.add_node("wizard",        wizard_node)

    g.set_entry_point("router")
    g.add_conditional_edges(
        "router",
        lambda s: s["route"],
        {"wizard": "wizard", "tool_task": "tool_task",
         "direct_answer": "direct_answer", "cant_do": "cant_do"},
    )
    g.add_edge("tool_task",     END)
    g.add_edge("direct_answer", END)
    g.add_edge("cant_do",       END)
    g.add_conditional_edges(
        "wizard",
        lambda s: "wizard" if s.get("wizard_active") else END,
        {"wizard": "wizard", END: END},
    )

    checkpointer = SqliteSaver.from_conn_string(_DB_PATH)
    return g.compile(checkpointer=checkpointer)


@lru_cache(maxsize=1)
def get_agent():
    return build_graph()


def run_agent(
    query: str,
    thread_id: str,
    on_token: Callable[[str], None] | None = None,
    model_override: str | None = None,
) -> tuple[str, str, str]:
    """Returns (reply_text, route, intent)."""
    agent = get_agent()
    config = {"configurable": {"thread_id": thread_id}}
    initial: dict = dict(
        messages=[HumanMessage(content=query)],
        thread_id=thread_id,
        route="",
        intent="",
        routed_model=model_override or "",
        tool_result="",
        tool_error=None,
        direct_answer="",
        wizard_active=False,
        wizard_workflow="",
        wizard_collected={},
        wizard_missing=[],
        wizard_step="",
        cant_do_reason="",
    )
    if on_token:
        initial["_token_cb"] = on_token  # transient, not in TypedDict, not persisted

    # Merge with persisted state (wizard may be mid-session)
    final = agent.invoke(initial, config=config)

    reply = (
        final.get("tool_result")
        or final.get("direct_answer")
        or final.get("cant_do_reason")
        or final["messages"][-1].content  # wizard's last AIMessage
    )
    return reply, final.get("route", ""), final.get("intent", "")
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_agent_graph.py -v
```

Expected: all `PASSED`

- [ ] **Step 5: Commit**

```bash
git add agents/graph.py tests/test_agent_graph.py
git commit -m "feat(agents): wire main LangGraph graph with SqliteSaver"
```

---

## Task 9: /agent/stream Endpoint in chat_ui.py

**Files:**
- Modify: `tools-harness/chat_ui.py`

- [ ] **Step 1: Locate insertion point**

```bash
grep -n "def chat_stream\|@app.get.*chat/stream\|# ── Agent" tools-harness/chat_ui.py | tail -5
```

Note the line number of the end of the `/chat/stream` route. The new endpoint goes immediately after it.

- [ ] **Step 2: Add /agent/stream endpoint**

After the closing of the `/chat/stream` route, add:

```python
# ---------------------------------------------------------------------------
# Agent graph endpoint — LangGraph-backed, supports wizard + tool tasks
# ---------------------------------------------------------------------------

@app.get("/agent/stream")
async def agent_stream(
    message: str,
    chat_id: str = "agent_web",
    file_ids: str = "",
    model: str = "",
):
    """SSE stream for LangGraph agent. Token format identical to /chat/stream."""
    import queue as _q
    import threading as _th
    from agents.graph import run_agent

    token_queue: _q.Queue = _q.Queue()
    result: dict = {}

    ids = [i for i in file_ids.split(",") if i.strip()] if file_ids else []
    full_message = (_build_file_context(ids) + message) if ids else message

    def on_token(t: str) -> None:
        token_queue.put(t)

    def _run() -> None:
        try:
            reply, route, intent = run_agent(
                query=full_message,
                thread_id=chat_id,
                on_token=on_token,
                model_override=model or None,
            )
            result.update(reply=reply, route=route, intent=intent)
        except Exception as exc:
            result["error"] = str(exc)
            _log.error("agent/stream chat_id=%s error=%s", chat_id, exc, exc_info=True)
        finally:
            token_queue.put(None)

    _th.Thread(target=_run, daemon=True).start()

    async def generate():
        loop = asyncio.get_event_loop()
        while True:
            try:
                token = await loop.run_in_executor(None, lambda: token_queue.get(timeout=300))
            except _q.Empty:
                yield f"data: {json.dumps({'done': True, 'error': 'timeout'})}\n\n"
                return
            if token is None:
                if "error" in result:
                    yield f"data: {json.dumps({'error': result['error']})}\n\n"
                else:
                    yield f"data: {json.dumps({'done': True, 'route': result.get('route',''), 'intent': result.get('intent','')})}\n\n"
                return
            yield f"data: {json.dumps({'token': token})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
```

- [ ] **Step 3: Verify server starts cleanly**

```bash
cd tools-harness && python -c "import chat_ui; print('import ok')"
```

Expected: `import ok`

- [ ] **Step 4: Smoke test endpoint (server must be running)**

```bash
curl -sN 'http://localhost:9234/agent/stream?message=what+is+2%2B2&chat_id=smoke_test' | head -5
```

Expected: SSE lines with `{"token": "..."}` followed by `{"done": true, ...}`

- [ ] **Step 5: Commit**

```bash
git add chat_ui.py
git commit -m "feat(chat_ui): add /agent/stream SSE endpoint for LangGraph agent"
```

---

## Task 10: Full Integration Tests

**Files:**
- Create: `tools-harness/tests/test_agent_integration.py`

- [ ] **Step 1: Write integration tests**

Create `tools-harness/tests/test_agent_integration.py`:

```python
"""
Integration tests: full graph invocations with real vault (mocked) and real graph.
No live Ollama required — all LLM calls are patched.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from unittest.mock import patch
from agents.graph import run_agent


def test_wizard_multi_turn_example_workflow():
    """Full 2-turn credential collection for example_workflow."""
    thread = "integration_example_workflow_01"
    with patch("agents.nodes.wizard.wizard_node.vault") as mock_vault, \
         patch("agents.nodes.wizard.register._register_workflow"):
        mock_vault.has.return_value = False
        mock_vault.set.return_value = None

        # Turn 1: trigger wizard
        reply1, route1, _ = run_agent("set up example_workflow", thread_id=thread)
        assert route1 == "wizard"
        assert "email" in reply1.lower() or "example_workflow" in reply1.lower()

        # Turn 2: provide email
        reply2, route2, _ = run_agent("user@example.com", thread_id=thread)
        assert route2 == "wizard"
        assert "password" in reply2.lower()

        # Turn 3: provide password
        reply3, route3, _ = run_agent("mypassword123", thread_id=thread)
        assert route3 == "wizard"
        assert "active" in reply3.lower() or "saved" in reply3.lower()


def test_cant_do_phone_call():
    reply, route, _ = run_agent("call me on my phone", thread_id="integration_cant_01")
    assert route == "cant_do"
    assert "Email" in reply  # capability list must mention Email


def test_tool_task_web_search():
    with patch("agents.nodes.router_node.classify", return_value=("mistral-nemo", "temporal")), \
         patch("agents.nodes.tool_task_node.harness_run", return_value="Latest AI news: ..."):
        reply, route, intent = run_agent("latest AI news", thread_id="integration_tool_01")
    assert route == "tool_task"
    assert intent == "temporal"
```

- [ ] **Step 2: Run all agent tests**

```bash
cd tools-harness && python -m pytest tests/test_agent_state.py tests/test_agent_cant_do.py tests/test_agent_router.py tests/test_agent_wizard.py tests/test_agent_graph.py tests/test_agent_integration.py -v
```

Expected: all `PASSED`

- [ ] **Step 3: Run full test suite to confirm no regressions**

```bash
python -m pytest tests/ -v --ignore=tests/test_agent_integration.py -x
```

Expected: same pass count as before this feature branch.

- [ ] **Step 4: Final commit**

```bash
git add tests/test_agent_integration.py
git commit -m "test(agents): add multi-turn wizard integration tests"
```

---

## Verification

```bash
# All agent tests
cd tools-harness && python -m pytest tests/test_agent_*.py -v

# Full suite — no regressions
python -m pytest tests/ -v

# Live SSE smoke (server running)
curl -sN 'http://localhost:9234/agent/stream?message=search+latest+AI+news&chat_id=live_test'
curl -sN 'http://localhost:9234/agent/stream?message=call+me+on+my+phone&chat_id=live_test2'
curl -sN 'http://localhost:9234/agent/stream?message=set+up+example_workflow&chat_id=wizard_test'
```
