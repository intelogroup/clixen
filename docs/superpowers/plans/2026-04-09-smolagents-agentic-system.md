# G4L Smolagents Agentic System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the raw Ollama tool loop (`ollama_client.chat()`) with a smolagents multi-agent system: a `CodeAgent` brain (qwen3:14b) that orchestrates three specialized `ToolCallingAgent`s (search, personal, code) for all tool-bearing intents.

**Architecture:** `harness.py` still routes via `classify()`, but tool-bearing intents now call `agents/dispatcher.py` instead of `ollama_client.chat()`. The dispatcher maps intent to the right specialized `ToolCallingAgent`. For complex multi-step tasks the brain `CodeAgent` manages all sub-agents. No-tool paths (casual, code_quick) still go direct to `ollama_client.chat()` — no smolagents overhead.

**Tech Stack:** `smolagents[litellm]`, `LiteLLMModel` with `ollama_chat/` prefix, `ToolCallingAgent`, `CodeAgent`, `@tool` decorator wrapping existing `tools/registry.py` executors.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `agents/__init__.py` | Create | Package marker |
| `agents/models.py` | Create | `LiteLLMModel` factory — one function per model |
| `agents/tool_wrappers.py` | Create | `@tool`-decorated wrappers for every executor in `registry.py` |
| `agents/search_agent.py` | Create | `ToolCallingAgent` (mistral-nemo): web_search, tech_search, brave_search, google_search, fixtures |
| `agents/personal_agent.py` | Create | `ToolCallingAgent` (mistral-nemo): email, calendar, tasks, reminder, get_current_time |
| `agents/code_agent.py` | Create | `ToolCallingAgent` (qwen3:14b): filesystem, git, repl, shell, semantic, context7 |
| `agents/brain.py` | Create | `CodeAgent` (qwen3:14b) — manager that orchestrates all three sub-agents |
| `agents/dispatcher.py` | Create | `dispatch(query, intent, on_token)` routes to right agent, returns `(result, model, intent)` |
| `harness.py` | Modify | Replace `local_chat()` call with `dispatcher.dispatch()` for tool-bearing intents |
| `clients/ollama_client.py` | No change | Still used for no-tool paths |

---

## Task 1: Install dependency and create package

**Files:**
- Create: `tools-harness/agents/__init__.py`

- [ ] **Step 1: Install smolagents with litellm extra**

```bash
pip install 'smolagents[litellm]'
```

Verify:

```bash
python -c "from smolagents import ToolCallingAgent, CodeAgent, LiteLLMModel; print('ok')"
```

Expected output: `ok`

- [ ] **Step 2: Create package marker**

Create `tools-harness/agents/__init__.py` as an empty file.

- [ ] **Step 3: Commit**

```bash
cd ~/Developer/clixen
git add tools-harness/agents/__init__.py
git commit -m "feat: add agents package skeleton"
```

---

## Task 2: Model factory

**Files:**
- Create: `tools-harness/agents/models.py`
- Create: `tools-harness/agents/test_models.py`

- [ ] **Step 1: Write the failing test**

`tools-harness/agents/test_models.py`:

```python
import pytest
from agents.models import get_model

def test_get_model_qwen3_14b():
    m = get_model("qwen3:14b")
    assert m.model_id == "ollama_chat/qwen3:14b"

def test_get_model_nemo():
    m = get_model("mistral-nemo")
    assert m.model_id == "ollama_chat/mistral-nemo"

def test_get_model_qwen3_4b():
    m = get_model("qwen3:4b")
    assert m.model_id == "ollama_chat/qwen3:4b"

def test_unknown_model_raises():
    with pytest.raises(ValueError):
        get_model("nonexistent-model")
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd ~/Developer/clixen/tools-harness
python -m pytest agents/test_models.py -v
```

Expected: `ImportError` — module doesn't exist yet.

- [ ] **Step 3: Implement models.py**

`tools-harness/agents/models.py`:

```python
from smolagents import LiteLLMModel

_OLLAMA_BASE = "http://localhost:11434"
_CTX = {
    "qwen3:14b":    32768,
    "qwen3:4b":     32768,
    "mistral-nemo": 32768,
    "gemma4":       32768,
}

def get_model(name: str) -> LiteLLMModel:
    """Return a LiteLLMModel for the given Ollama model name.

    Args:
        name: Ollama model name, e.g. 'qwen3:14b', 'mistral-nemo'
    Raises:
        ValueError: if model is not in the supported set
    """
    if name not in _CTX:
        raise ValueError(f"Unsupported model: {name!r}. Supported: {sorted(_CTX)}")
    return LiteLLMModel(
        model_id=f"ollama_chat/{name}",
        api_base=_OLLAMA_BASE,
        api_key="ollama",
        num_ctx=_CTX[name],
    )
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest agents/test_models.py -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools-harness/agents/models.py tools-harness/agents/test_models.py
git commit -m "feat: add LiteLLMModel factory for Ollama"
```

---

## Task 3: Tool wrappers

**Files:**
- Create: `tools-harness/agents/tool_wrappers.py`
- Create: `tools-harness/agents/test_tool_wrappers.py`

Wrap all existing `registry.py` executors with `@tool`. The decorator reads the function signature, type hints, and docstring to build the tool schema — every arg needs a type hint and the docstring needs an `Args:` section.

- [ ] **Step 1: Write the failing test**

`tools-harness/agents/test_tool_wrappers.py`:

```python
from smolagents import Tool
from agents.tool_wrappers import (
    web_search, tech_search, brave_search, google_search,
    get_current_time,
    list_emails, read_email,
    list_calendar_events,
    read_file, grep_files, bash_exec,
)

def test_all_wrappers_are_tool_instances():
    for t in [web_search, tech_search, brave_search, google_search,
              get_current_time, list_emails, read_email,
              list_calendar_events, read_file, grep_files, bash_exec]:
        assert isinstance(t, Tool), f"{t} is not a smolagents Tool"

def test_web_search_schema():
    assert web_search.name == "web_search"
    assert "query" in web_search.inputs
    assert web_search.output_type == "string"
```

- [ ] **Step 2: Run test — expect failure**

```bash
python -m pytest agents/test_tool_wrappers.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement tool_wrappers.py**

`tools-harness/agents/tool_wrappers.py`:

```python
"""smolagents @tool wrappers around existing registry executors."""
from smolagents import tool
from tools.registry import (
    tavily_execute, _exa_execute, serp_execute, brave_execute, fixtures_execute,
    get_current_time as _time, set_reminder as _set_reminder,
    list_emails as _list_emails, read_email as _read_email, send_email as _send_email,
    refresh_google_token as _refresh,
    list_calendar_events as _list_events, create_calendar_event as _create_event,
    delete_calendar_event as _delete_event,
    list_tasks as _list_tasks, create_task as _create_task,
    complete_task as _complete_task, delete_task as _delete_task,
    read_file as _read_file, grep_files as _grep, find_files as _find,
    list_directory as _ls, file_tree as _tree, read_many_files as _read_many,
    parse_file as _parse, read_pdf as _pdf, parse_code as _parse_code,
    index_directory as _index, semantic_file_search as _sem_search,
    bash_exec as _bash, write_file as _write, edit_file as _edit, append_file as _append,
    git_status as _git_status, git_diff as _git_diff, git_log as _git_log,
    git_add as _git_add, git_commit as _git_commit,
    git_checkout as _git_checkout, git_new_worktree as _git_worktree,
    run_python as _run_python, reset_kernel as _reset_kernel,
    list_kernel_vars as _kernel_vars,
    context7_execute as _context7,
)

# --- Search ---

@tool
def web_search(query: str, max_results: int = 3) -> str:
    """Search the web for current news, prices, weather, or recent events. Use this for any time-sensitive query.
    Args:
        query: The search query. Include 'latest' or the current year for time-sensitive topics.
        max_results: Number of results to return (default 3).
    """
    return tavily_execute(query=query, max_results=max_results)

@tool
def tech_search(query: str, max_results: int = 3) -> str:
    """Neural search for technical content: GitHub repos, library versions, release notes, API docs, npm/pypi packages.
    Args:
        query: Technical search query.
        max_results: Number of results to return (default 3).
    """
    return _exa_execute(query=query, max_results=max_results)

@tool
def google_search(query: str, engine: str = "google") -> str:
    """Search via SerpAPI. Best for live sports scores, finance, and structured SERP results.
    Args:
        query: Search query.
        engine: Search engine to use (default 'google').
    """
    return serp_execute(query=query, engine=engine)

@tool
def brave_search(query: str, freshness: str = "") -> str:
    """Search via Brave. Best for local places, science news, and current events.
    Args:
        query: Search query.
        freshness: Optional freshness filter e.g. 'pd' (past day), 'pw' (past week).
    """
    return brave_execute(query=query, freshness=freshness)

@tool
def get_fixtures(query: str, days: int = 14) -> str:
    """Get upcoming sports fixtures and match schedules.
    Args:
        query: Sport or team name.
        days: Days ahead to look (default 14).
    """
    return fixtures_execute(query=query, days=days)

# --- Time / Reminder ---

@tool
def get_current_time() -> str:
    """Returns the current date, time, and timezone. Always call this before scheduling or creating calendar events."""
    return _time()

@tool
def set_reminder(title: str, remind_at: str, notes: str = "") -> str:
    """Set a local reminder.
    Args:
        title: Reminder title.
        remind_at: ISO 8601 datetime string e.g. '2026-04-10T09:00:00+02:00'.
        notes: Optional notes.
    """
    return _set_reminder(title=title, remind_at=remind_at, notes=notes)

# --- Gmail ---

@tool
def list_emails(query: str = "", max_results: int = 10) -> str:
    """List Gmail emails. Filter with a Gmail search query.
    Args:
        query: Gmail search query e.g. 'is:unread', 'from:boss@example.com'.
        max_results: Max emails to return (default 10).
    """
    return _list_emails(query=query, max_results=max_results)

@tool
def read_email(message_id: str) -> str:
    """Read the full body of a Gmail message.
    Args:
        message_id: The Gmail message ID from list_emails.
    """
    return _read_email(message_id=message_id)

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email via Gmail.
    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain-text email body.
    """
    return _send_email(to=to, subject=subject, body=body)

@tool
def refresh_google_token() -> str:
    """Refresh the Google OAuth2 token. Call this if Gmail/Calendar/Tasks tools return auth errors."""
    return _refresh()

# --- Google Calendar ---

@tool
def list_calendar_events(time_min: str = "", time_max: str = "", max_results: int = 10) -> str:
    """List upcoming Google Calendar events.
    Args:
        time_min: ISO 8601 start datetime (default now).
        time_max: ISO 8601 end datetime (default 7 days from now).
        max_results: Max events to return (default 10).
    """
    return _list_events(time_min=time_min, time_max=time_max, max_results=max_results)

@tool
def create_calendar_event(title: str, start: str, end: str, description: str = "") -> str:
    """Create a Google Calendar event. Always call get_current_time first for relative dates.
    Args:
        title: Event title.
        start: ISO 8601 start datetime e.g. '2026-04-10T10:00:00+02:00'.
        end: ISO 8601 end datetime.
        description: Optional event description.
    """
    return _create_event(title=title, start=start, end=end, description=description)

@tool
def delete_calendar_event(event_id: str) -> str:
    """Delete a Google Calendar event by ID.
    Args:
        event_id: The event ID from list_calendar_events.
    """
    return _delete_event(event_id=event_id)

# --- Google Tasks ---

@tool
def list_tasks(tasklist_id: str = "@default") -> str:
    """List Google Tasks.
    Args:
        tasklist_id: Task list ID (default '@default').
    """
    return _list_tasks(tasklist_id=tasklist_id)

@tool
def create_task(title: str, due: str = "", notes: str = "") -> str:
    """Create a Google Task.
    Args:
        title: Task title.
        due: Optional ISO 8601 due date e.g. '2026-04-11T00:00:00Z'.
        notes: Optional notes.
    """
    return _create_task(title=title, due=due, notes=notes)

@tool
def complete_task(task_id: str, tasklist_id: str = "@default") -> str:
    """Mark a Google Task as completed.
    Args:
        task_id: Task ID from list_tasks.
        tasklist_id: Task list ID (default '@default').
    """
    return _complete_task(task_id=task_id, tasklist_id=tasklist_id)

@tool
def delete_task(task_id: str, tasklist_id: str = "@default") -> str:
    """Delete a Google Task.
    Args:
        task_id: Task ID from list_tasks.
        tasklist_id: Task list ID (default '@default').
    """
    return _delete_task(task_id=task_id, tasklist_id=tasklist_id)

# --- Filesystem (read) ---

@tool
def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
    """Read lines from a file.
    Args:
        path: Absolute file path.
        offset: First line to read (1-indexed, default 1).
        limit: Max lines to read (default 200).
    """
    return _read_file(path=path, offset=offset, limit=limit)

@tool
def grep_files(pattern: str, path: str, glob: str = "*", max_results: int = 30, ignore_case: bool = False) -> str:
    """Search file contents with a regex pattern.
    Args:
        pattern: Regex pattern to search for.
        path: Directory to search in.
        glob: Glob filter for file names (default '*').
        max_results: Max matching lines (default 30).
        ignore_case: Case-insensitive search (default False).
    """
    return _grep(pattern=pattern, path=path, glob=glob, max_results=max_results, ignore_case=ignore_case)

@tool
def find_files(pattern: str, path: str, max_results: int = 20) -> str:
    """Find files by name glob pattern.
    Args:
        pattern: Glob pattern e.g. '*.py', 'test_*'.
        path: Directory to search in.
        max_results: Max results (default 20).
    """
    return _find(pattern=pattern, path=path, max_results=max_results)

@tool
def list_directory(path: str, show_hidden: bool = False) -> str:
    """List contents of a directory.
    Args:
        path: Directory path.
        show_hidden: Include hidden files (default False).
    """
    return _ls(path=path, show_hidden=show_hidden)

@tool
def file_tree(path: str, max_depth: int = 3) -> str:
    """Show a tree view of a directory.
    Args:
        path: Root directory path.
        max_depth: Max depth to traverse (default 3).
    """
    return _tree(path=path, max_depth=max_depth)

@tool
def read_many_files(paths: list) -> str:
    """Read multiple files and return their contents concatenated.
    Args:
        paths: List of absolute file paths.
    """
    return _read_many(paths=paths)

@tool
def parse_file(path: str) -> str:
    """Parse a structured file (CSV, JSON, YAML, TOML) into readable text.
    Args:
        path: Absolute file path.
    """
    return _parse(path=path)

@tool
def read_pdf(path: str) -> str:
    """Extract text from a PDF file.
    Args:
        path: Absolute path to the PDF file.
    """
    return _pdf(path=path)

@tool
def parse_code(path: str) -> str:
    """Parse a source code file and extract classes and functions.
    Args:
        path: Absolute path to the source file.
    """
    return _parse_code(path=path)

@tool
def index_directory(path: str) -> str:
    """Index a directory for semantic file search.
    Args:
        path: Directory to index.
    """
    return _index(path=path)

@tool
def semantic_file_search(query: str, path: str, top_k: int = 5) -> str:
    """Search files by meaning using vector embeddings. Requires index_directory first.
    Args:
        query: Natural language search query.
        path: Directory to search (must be indexed first with index_directory).
        top_k: Number of results (default 5).
    """
    return _sem_search(query=query, path=path, top_k=top_k)

@tool
def get_library_docs(library: str, query: str) -> str:
    """Fetch up-to-date documentation for a library or framework via Context7.
    Args:
        library: Library name e.g. 'fastapi', 'react', 'pydantic'.
        query: What you want to know about the library.
    """
    return _context7(library=library, query=query)

# --- Filesystem (write) ---

@tool
def bash_exec(command: str, cwd: str = "") -> str:
    """Execute a shell command.
    Args:
        command: Shell command to run.
        cwd: Working directory (default: home directory).
    """
    return _bash(command=command, cwd=cwd)

@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file, overwriting if it exists.
    Args:
        path: Absolute file path.
        content: File content to write.
    """
    return _write(path=path, content=content)

@tool
def edit_file(path: str, old_str: str, new_str: str) -> str:
    """Edit a file by replacing an exact string. old_str must be unique in the file.
    Args:
        path: Absolute file path.
        old_str: Exact string to find and replace.
        new_str: Replacement string.
    """
    return _edit(path=path, old_str=old_str, new_str=new_str)

@tool
def append_file(path: str, content: str) -> str:
    """Append content to the end of a file.
    Args:
        path: Absolute file path.
        content: Content to append.
    """
    return _append(path=path, content=content)

# --- Git ---

@tool
def git_status(repo_path: str) -> str:
    """Show git working tree status.
    Args:
        repo_path: Absolute path to the git repository.
    """
    return _git_status(repo_path=repo_path)

@tool
def git_diff(repo_path: str, staged: bool = False) -> str:
    """Show git diff.
    Args:
        repo_path: Absolute path to the git repository.
        staged: Show staged diff (default False).
    """
    return _git_diff(repo_path=repo_path, staged=staged)

@tool
def git_log(repo_path: str, n: int = 10) -> str:
    """Show recent git commits.
    Args:
        repo_path: Absolute path to the git repository.
        n: Number of commits to show (default 10).
    """
    return _git_log(repo_path=repo_path, n=n)

@tool
def git_add(repo_path: str, paths: list) -> str:
    """Stage files for commit.
    Args:
        repo_path: Absolute path to the git repository.
        paths: List of file paths to stage.
    """
    return _git_add(repo_path=repo_path, paths=paths)

@tool
def git_commit(repo_path: str, message: str) -> str:
    """Create a git commit.
    Args:
        repo_path: Absolute path to the git repository.
        message: Commit message.
    """
    return _git_commit(repo_path=repo_path, message=message)

@tool
def git_checkout(repo_path: str, branch: str, create: bool = False) -> str:
    """Checkout a git branch.
    Args:
        repo_path: Absolute path to the git repository.
        branch: Branch name to checkout.
        create: Create new branch if True (default False).
    """
    return _git_checkout(repo_path=repo_path, branch=branch, create=create)

@tool
def git_new_worktree(repo_path: str, branch: str, path: str) -> str:
    """Create a new git worktree.
    Args:
        repo_path: Absolute path to the git repository.
        branch: Branch name for the worktree.
        path: Where to create the worktree.
    """
    return _git_worktree(repo_path=repo_path, branch=branch, path=path)

# --- Python REPL ---

@tool
def run_python(code: str) -> str:
    """Execute Python code in a persistent kernel and return output.
    Args:
        code: Python code to execute.
    """
    return _run_python(code=code)

@tool
def reset_kernel() -> str:
    """Reset the Python REPL kernel, clearing all variables and imports."""
    return _reset_kernel()

@tool
def list_kernel_vars() -> str:
    """List all variables currently defined in the Python REPL kernel."""
    return _kernel_vars()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest agents/test_tool_wrappers.py -v
```

Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools-harness/agents/tool_wrappers.py tools-harness/agents/test_tool_wrappers.py
git commit -m "feat: wrap all registry executors as smolagents @tool"
```

---

## Task 4: Specialized sub-agents

**Files:**
- Create: `tools-harness/agents/search_agent.py`
- Create: `tools-harness/agents/personal_agent.py`
- Create: `tools-harness/agents/code_agent.py`
- Create: `tools-harness/agents/test_sub_agents.py`

- [ ] **Step 1: Write the failing test**

`tools-harness/agents/test_sub_agents.py`:

```python
from smolagents import ToolCallingAgent
from agents.search_agent import make_search_agent
from agents.personal_agent import make_personal_agent
from agents.code_agent import make_code_agent

def test_search_agent():
    agent = make_search_agent()
    assert isinstance(agent, ToolCallingAgent)
    names = [t.name for t in agent.tools.values()]
    assert "web_search" in names
    assert "tech_search" in names

def test_personal_agent():
    agent = make_personal_agent()
    assert isinstance(agent, ToolCallingAgent)
    names = [t.name for t in agent.tools.values()]
    assert "list_emails" in names
    assert "list_calendar_events" in names
    assert "get_current_time" in names

def test_code_agent():
    agent = make_code_agent()
    assert isinstance(agent, ToolCallingAgent)
    names = [t.name for t in agent.tools.values()]
    assert "read_file" in names
    assert "bash_exec" in names
    assert "git_status" in names
```

- [ ] **Step 2: Run test — expect failure**

```bash
python -m pytest agents/test_sub_agents.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create search_agent.py**

`tools-harness/agents/search_agent.py`:

```python
from smolagents import ToolCallingAgent
from agents.models import get_model
from agents.tool_wrappers import (
    web_search, tech_search, google_search, brave_search,
    get_fixtures, get_current_time,
)

_SYSTEM = (
    "You are a web search specialist. For ANY time-sensitive question call the "
    "appropriate search tool FIRST, then answer ONLY from the results. "
    "NEVER answer from memory for current information. "
    "Choose: web_search for general, tech_search for code/repos, "
    "google_search for sports/finance, brave_search for local/science."
)

def make_search_agent() -> ToolCallingAgent:
    return ToolCallingAgent(
        tools=[web_search, tech_search, google_search, brave_search, get_fixtures, get_current_time],
        model=get_model("mistral-nemo"),
        max_steps=5,
        name="search_agent",
        description="Searches the web for current information. Give it your query as a plain string.",
        system_prompt=_SYSTEM,
    )
```

- [ ] **Step 4: Create personal_agent.py**

`tools-harness/agents/personal_agent.py`:

```python
from smolagents import ToolCallingAgent
from agents.models import get_model
from agents.tool_wrappers import (
    get_current_time, set_reminder,
    list_emails, read_email, send_email, refresh_google_token,
    list_calendar_events, create_calendar_event, delete_calendar_event,
    list_tasks, create_task, complete_task, delete_task,
)

_SYSTEM = (
    "You are a personal assistant with Gmail, Google Calendar, and Google Tasks access. "
    "ALWAYS call get_current_time before creating events or tasks with relative dates. "
    "NEVER invent IDs or email addresses — fetch them with list tools first. "
    "If required fields are missing, ask the user — never assume."
)

def make_personal_agent() -> ToolCallingAgent:
    return ToolCallingAgent(
        tools=[
            get_current_time, set_reminder,
            list_emails, read_email, send_email, refresh_google_token,
            list_calendar_events, create_calendar_event, delete_calendar_event,
            list_tasks, create_task, complete_task, delete_task,
        ],
        model=get_model("mistral-nemo"),
        max_steps=8,
        name="personal_agent",
        description="Manages email, calendar events, tasks, and reminders. Give it your request in plain language.",
        system_prompt=_SYSTEM,
    )
```

- [ ] **Step 5: Create code_agent.py**

`tools-harness/agents/code_agent.py`:

```python
from smolagents import ToolCallingAgent
from agents.models import get_model
from agents.tool_wrappers import (
    read_file, grep_files, find_files, list_directory, file_tree,
    read_many_files, parse_file, read_pdf, parse_code,
    index_directory, semantic_file_search,
    bash_exec, write_file, edit_file, append_file,
    git_status, git_diff, git_log, git_add, git_commit,
    git_checkout, git_new_worktree,
    run_python, reset_kernel, list_kernel_vars,
    get_library_docs,
)

_SYSTEM = (
    "You are a coding agent with full filesystem, shell, and git access. "
    "For single-file tasks: read first, then edit_file or write_file. "
    "Only call file_tree when you need to understand project layout. "
    "After editing, verify with bash_exec if the user asked to run or test. "
    "Be concise — report what changed, not what you read."
)

def make_code_agent() -> ToolCallingAgent:
    return ToolCallingAgent(
        tools=[
            read_file, grep_files, find_files, list_directory, file_tree,
            read_many_files, parse_file, read_pdf, parse_code,
            index_directory, semantic_file_search,
            bash_exec, write_file, edit_file, append_file,
            git_status, git_diff, git_log, git_add, git_commit,
            git_checkout, git_new_worktree,
            run_python, reset_kernel, list_kernel_vars,
            get_library_docs,
        ],
        model=get_model("qwen3:14b"),
        max_steps=15,
        name="code_agent",
        description="Reads/writes files, runs shell commands, executes Python, manages git.",
        system_prompt=_SYSTEM,
    )
```

- [ ] **Step 6: Run tests — expect pass**

```bash
python -m pytest agents/test_sub_agents.py -v
```

Expected: 3 PASSED.

- [ ] **Step 7: Commit**

```bash
git add tools-harness/agents/search_agent.py \
        tools-harness/agents/personal_agent.py \
        tools-harness/agents/code_agent.py \
        tools-harness/agents/test_sub_agents.py
git commit -m "feat: add search, personal, and code ToolCallingAgents"
```

---

## Task 5: Brain (manager CodeAgent)

**Files:**
- Create: `tools-harness/agents/brain.py`
- Create: `tools-harness/agents/test_brain.py`

- [ ] **Step 1: Write the failing test**

`tools-harness/agents/test_brain.py`:

```python
from smolagents import CodeAgent
from agents.brain import make_brain

def test_brain_is_code_agent():
    brain = make_brain()
    assert isinstance(brain, CodeAgent)

def test_brain_has_all_managed_agents():
    brain = make_brain()
    names = [a.name for a in brain.managed_agents]
    assert "search_agent" in names
    assert "personal_agent" in names
    assert "code_agent" in names
```

- [ ] **Step 2: Run test — expect failure**

```bash
python -m pytest agents/test_brain.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement brain.py**

`tools-harness/agents/brain.py`:

```python
from smolagents import CodeAgent
from agents.models import get_model
from agents.search_agent import make_search_agent
from agents.personal_agent import make_personal_agent
from agents.code_agent import make_code_agent

_SYSTEM = (
    "You are the orchestrating brain of a local AI assistant. "
    "You have three managed sub-agents:\n"
    "- search_agent: web search, news, prices, repos, docs\n"
    "- personal_agent: email, calendar, tasks, reminders\n"
    "- code_agent: filesystem, code editing, git, Python REPL\n\n"
    "For complex tasks requiring multiple agents, chain them in sequence. "
    "For single-domain tasks, delegate to the appropriate specialist directly. "
    "Always summarise results clearly for the user."
)

def make_brain() -> CodeAgent:
    return CodeAgent(
        tools=[],
        model=get_model("qwen3:14b"),
        managed_agents=[make_search_agent(), make_personal_agent(), make_code_agent()],
        max_steps=20,
        additional_authorized_imports=["datetime", "json", "re"],
        system_prompt=_SYSTEM,
    )
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest agents/test_brain.py -v
```

Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools-harness/agents/brain.py tools-harness/agents/test_brain.py
git commit -m "feat: add CodeAgent brain with managed sub-agents"
```

---

## Task 6: Dispatcher

**Files:**
- Create: `tools-harness/agents/dispatcher.py`
- Create: `tools-harness/agents/test_dispatcher.py`

- [ ] **Step 1: Write the failing test**

`tools-harness/agents/test_dispatcher.py`:

```python
from agents.dispatcher import route_to_agent

def test_temporal_routes_to_search():
    assert route_to_agent("temporal")[0] == "search_agent"

def test_temporal_news_routes_to_search():
    assert route_to_agent("temporal_news")[0] == "search_agent"

def test_email_routes_to_personal():
    assert route_to_agent("email")[0] == "personal_agent"

def test_calendar_routes_to_personal():
    assert route_to_agent("calendar")[0] == "personal_agent"

def test_filesystem_routes_to_code():
    assert route_to_agent("filesystem")[0] == "code_agent"

def test_git_routes_to_code():
    assert route_to_agent("git")[0] == "code_agent"

def test_analysis_routes_to_brain():
    assert route_to_agent("analysis")[0] == "brain"

def test_casual_returns_none():
    assert route_to_agent("casual")[0] is None

def test_code_quick_returns_none():
    assert route_to_agent("code_quick")[0] is None
```

- [ ] **Step 2: Run test — expect failure**

```bash
python -m pytest agents/test_dispatcher.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement dispatcher.py**

`tools-harness/agents/dispatcher.py`:

```python
"""Dispatcher — maps intent to the right smolagents agent."""
from __future__ import annotations
import datetime
from typing import Callable, Optional

_SEARCH_INTENTS = {
    "temporal", "temporal_news", "temporal_sports", "temporal_finance",
    "temporal_weather", "temporal_tech", "temporal_local", "temporal_science",
    "web_search",
}
_PERSONAL_INTENTS = {"email", "calendar", "tasks", "reminder"}
_CODE_INTENTS = {"filesystem", "git", "repl", "ide", "code_heavy", "library_docs"}
_BRAIN_INTENTS = {"analysis", "math", "multilingual", "ocr"}
_PASSTHROUGH_INTENTS = {"casual", "code_quick", "code_medium"}

_MODEL_FOR_AGENT = {
    "search_agent":   "mistral-nemo",
    "personal_agent": "mistral-nemo",
    "code_agent":     "qwen3:14b",
    "brain":          "qwen3:14b",
}

# Agent instance cache — build once per process
_agents: dict = {}


def route_to_agent(intent: str) -> tuple[Optional[str], list]:
    """Return (agent_name, []) for the given intent. None means use ollama_client directly."""
    if intent in _SEARCH_INTENTS:
        return "search_agent", []
    if intent in _PERSONAL_INTENTS:
        return "personal_agent", []
    if intent in _CODE_INTENTS:
        return "code_agent", []
    if intent in _BRAIN_INTENTS:
        return "brain", []
    return None, []


def _get_agent(name: str):
    if name not in _agents:
        if name == "search_agent":
            from agents.search_agent import make_search_agent
            _agents[name] = make_search_agent()
        elif name == "personal_agent":
            from agents.personal_agent import make_personal_agent
            _agents[name] = make_personal_agent()
        elif name == "code_agent":
            from agents.code_agent import make_code_agent
            _agents[name] = make_code_agent()
        elif name == "brain":
            from agents.brain import make_brain
            _agents[name] = make_brain()
    return _agents[name]


def dispatch(
    query: str,
    intent: str,
    on_token: Optional[Callable[[str], None]] = None,
) -> tuple[str, str, str]:
    """Run the appropriate agent and return (result, model_name, intent)."""
    agent_name, _ = route_to_agent(intent)
    if agent_name is None:
        raise ValueError(
            f"dispatch() called for passthrough intent '{intent}' — "
            "use ollama_client.chat() directly."
        )
    now = datetime.datetime.now().astimezone()
    dt_prefix = f"[{now.strftime('%A, %B %d, %Y at %H:%M %Z')}]\n\n"
    result = _get_agent(agent_name).run(dt_prefix + query)
    return str(result), _MODEL_FOR_AGENT[agent_name], intent
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest agents/test_dispatcher.py -v
```

Expected: 9 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools-harness/agents/dispatcher.py tools-harness/agents/test_dispatcher.py
git commit -m "feat: add dispatcher routing intent to smolagents"
```

---

## Task 7: Wire dispatcher into harness.py

**Files:**
- Modify: `tools-harness/harness.py`
- Create: `tools-harness/test_harness_dispatch.py`

- [ ] **Step 1: Write the failing test**

`tools-harness/test_harness_dispatch.py`:

```python
from unittest.mock import patch, MagicMock

def test_temporal_uses_dispatcher(monkeypatch):
    mock_dispatch = MagicMock(return_value=("result", "mistral-nemo", "temporal_news"))
    monkeypatch.setattr("harness.dispatcher.dispatch", mock_dispatch)
    import harness
    with patch("harness.classify", return_value=("mistral-nemo", "temporal")):
        with patch("harness.classify_search_backend", return_value=("tavily", "news")):
            result, model, intent = harness.run("what is in the news today?")
    mock_dispatch.assert_called_once()
    assert model == "mistral-nemo"

def test_casual_uses_local_chat(monkeypatch):
    mock_chat = MagicMock(return_value="hello back")
    monkeypatch.setattr("harness.local_chat", mock_chat)
    mock_dispatch = MagicMock()
    monkeypatch.setattr("harness.dispatcher.dispatch", mock_dispatch)
    import harness
    with patch("harness.classify", return_value=("qwen3:4b", "casual")):
        harness.run("hey how are you")
    mock_chat.assert_called_once()
    mock_dispatch.assert_not_called()
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd ~/Developer/clixen/tools-harness
python -m pytest test_harness_dispatch.py -v
```

Expected: FAIL — harness doesn't import dispatcher yet.

- [ ] **Step 3: Add dispatcher import to harness.py**

In `tools-harness/harness.py`, add after existing imports:

```python
from agents import dispatcher
```

- [ ] **Step 4: Replace the LLM call block**

Find the comment `# LLM call is outside the lock` in `harness.py` and replace the entire `result = local_chat(...)` block with:

```python
    # LLM call is outside the lock — it's slow and chat-independent
    # Tool-bearing intents use smolagents dispatcher; no-tool paths use ollama_client directly
    _agent_name, _ = dispatcher.route_to_agent(intent)
    if _agent_name is not None and active_tools:
        result, routed_model, intent = dispatcher.dispatch(
            query=query,
            intent=intent,
            on_token=on_token,
        )
    else:
        result = local_chat(
            user_message=query,
            tools=active_tools,
            model=routed_model,
            history=history,
            on_token=on_token,
            system_prompt=system_prompt,
            max_rounds=15 if _ide_override else MAX_ROUNDS,
            images=images or None,
        )
```

- [ ] **Step 5: Run tests — expect pass**

```bash
python -m pytest test_harness_dispatch.py -v
```

Expected: 2 PASSED.

- [ ] **Step 6: Smoke test — start server and send a temporal query**

```bash
cd ~/Developer/clixen/tools-harness
python chat_ui.py &
sleep 3
curl -s -X POST http://localhost:9234/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "what is the latest stable version of smolagents?", "chat_id": "smoke-1"}'
```

Expected: JSON with `result` containing a version string sourced from a search.

Kill the server after:

```bash
kill %1
```

- [ ] **Step 7: Commit**

```bash
git add tools-harness/harness.py tools-harness/test_harness_dispatch.py
git commit -m "feat: wire smolagents dispatcher into harness.run()"
```

---

## Task 8: Sync lowercase repo

**Files:**
- Sync: `~/developer/clixen/tools-harness/agents/`
- Sync: `~/developer/clixen/tools-harness/harness.py`

- [ ] **Step 1: Copy agents package**

```bash
cp -r ~/Developer/clixen/tools-harness/agents \
      ~/developer/clixen/tools-harness/agents
```

- [ ] **Step 2: Copy harness.py**

```bash
cp ~/Developer/clixen/tools-harness/harness.py \
   ~/developer/clixen/tools-harness/harness.py
```

- [ ] **Step 3: Restart Telegram bot**

```bash
launchctl unload ~/Library/LaunchAgents/com.clixen.telegrambot.plist
launchctl load ~/Library/LaunchAgents/com.clixen.telegrambot.plist
```

- [ ] **Step 4: Verify no import errors**

```bash
tail -20 ~/developer/clixen/tools-harness/telegram_bot_stderr.log
```

Expected: no `ImportError` or `ModuleNotFoundError`.

- [ ] **Step 5: Commit lowercase copy**

```bash
cd ~/developer/clixen
git add tools-harness/agents tools-harness/harness.py
git commit -m "sync: smolagents dispatcher + agents package"
```

---

## Self-Review

**Spec coverage:**
- qwen3:14b as main brain — CodeAgent + code_agent ToolCallingAgent
- mistral-nemo as dispatcher for search + personal
- qwen3:4b — casual/code_quick still use ollama_client with router's qwen3:4b choice (no overhead)
- Pydantic — smolagents uses Pydantic internally for @tool schema validation
- ToolCallingAgent for all atomic tool dispatch
- CodeAgent brain with managed_agents for multi-step orchestration
- Two-repo sync in Task 8
- bge-m3 / reranker — separate concern, separate plan (RAG pipeline)
- on_token streaming — reserved in dispatcher.dispatch() signature; smolagents step-level streaming addable later

**Placeholder scan:** No TBDs. All code blocks are complete implementations.

**Type consistency:** `route_to_agent()` returns `(str | None, list)` used consistently in dispatcher and harness. `dispatch()` returns `(str, str, str)` matching `harness.run()` contract throughout.
