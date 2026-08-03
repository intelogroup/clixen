"""Tier 0 — code-fact invariants. Deterministic, offline, no model.

Every assertion here corresponds to a claim CLAUDE.md makes about the system. These are
the claims that rot silently: nothing breaks when they stop being true, the docs just
start lying, and the next debugging session starts from a false premise.

If one of these fails, either the code regressed or the doc is stale. Both are bugs.
"""
from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path

import pytest

from agents import local_agent_graph, local_agent_tools
from clients import cost_guard, router
from clients.cloud_client import DEFAULT_CLOUD_MODEL
from tools import registry

_HARNESS = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ routing

_CLASSIFIERS = ("classify", "classify_telegram", "classify_ide", "classify_plan")


def _returned_models(fn_name: str) -> set[str]:
    """Every model literal a classifier can return, read from its source."""
    src = inspect.getsource(getattr(router, fn_name))
    # `return CLOUD_MODEL, "x"` / `return "gemma4:12b-mlx", "ocr"`
    return set(re.findall(r'return\s+([A-Za-z_0-9]+|"[^"]+"),\s*"[a-z_]+"', src))


@pytest.mark.parametrize("fn_name", _CLASSIFIERS)
def test_only_ocr_escapes_cloud_default(fn_name):
    """CLAUDE.md: every branch defaults to cloud, *except* ocr (only multimodal model)."""
    src = inspect.getsource(getattr(router, fn_name))
    local_returns = re.findall(r'return\s+"([^"]+)",\s*"([a-z_]+)"', src)
    offenders = [(m, i) for m, i in local_returns if i != "ocr"]
    assert not offenders, f"{fn_name} returns a hardcoded local model for non-ocr intents: {offenders}"


def test_model_for_intent_is_cloud_everywhere():
    for intent in ("casual", "math", "automation", "email", "code_heavy", "browser"):
        assert router.model_for_intent(intent) == DEFAULT_CLOUD_MODEL


@pytest.mark.xfail(
    strict=True,
    reason="FOUND BY EVAL: tech_brief still routes to qwen3.5:4b. CLAUDE.md claims "
    "qwen3.5:4b is 'not in TASK_ROUTING now' and that every branch defaults cloud. "
    "A ~4B model runs the tech_brief automation's tool chain — below the ~7B floor.",
)
def test_task_routing_is_cloud_everywhere():
    """TASK_ROUTING drives the job worker; a stray local model there means a background
    automation silently runs on a model that can't chain tools."""
    local = {
        task: cfg["model"]
        for task, cfg in router.TASK_ROUTING.items()
        if cfg.get("model") != DEFAULT_CLOUD_MODEL
    }
    assert not local, f"TASK_ROUTING entries not on cloud: {local}"


@pytest.mark.parametrize("fn_name", ["classify", "classify_telegram"])
def test_automation_branch_precedes_tasks_branch(fn_name):
    """CLAUDE.md: branch order is load-bearing — `automation` must be checked before the
    _TASKS_RE branch, or 'remind me every morning' classifies as a one-off task.

    Compares the first *returning* branch of each intent, not the first textual mention:
    _TASKS_RE is named inside the automation branch's own guard clauses.
    """
    src = inspect.getsource(getattr(router, fn_name))
    i_auto = src.find('return CLOUD_MODEL, "automation"')
    i_tasks = src.find('return CLOUD_MODEL, "tasks"')
    assert i_auto != -1 and i_tasks != -1, "branch shape changed — re-read router.py"
    assert i_auto < i_tasks, "automation branch moved below tasks — behavior change"


def test_ocr_is_the_only_multimodal_route():
    model, intent = router.classify("what does this screenshot say")
    assert intent == "ocr"
    assert model == "openrouter/google/gemini-2.5-flash"


# ------------------------------------------------------------------ toolsets

# Pinned to the live values as of 2026-07-10. CLAUDE.md said 24/37/47 — stale by 4/5/5
# tools, caught by this eval on its first run. A drift here means a tool's tags changed:
# either intentional (update this dict + CLAUDE.md) or a tool silently entered/left a
# task's toolset (a real regression — e.g. a browser tool leaking into `document`).
_EXPECTED_TOOL_COUNTS = {"document": 30, "code": 45, "full": 70}


@pytest.mark.parametrize("task,expected", _EXPECTED_TOOL_COUNTS.items())
def test_local_agent_toolset_sizes(task, expected):
    actual = len(local_agent_tools.get_local_agent_tools(task))
    assert actual == expected, (
        f"{task} toolset is {actual}, CLAUDE.md says {expected} — "
        "a tool's tags changed, or the doc is stale"
    )


def test_document_toolset_keeps_the_undo_safety_net():
    """append_file/undo_last_edit are tagged "code", not "core" — document mode pulls them
    in explicitly so a document run can revert a bad edit."""
    names = {t["function"]["name"] for t in local_agent_tools.get_local_agent_tools("document")}
    assert {"append_file", "undo_last_edit"} <= names


def test_step_caps():
    assert local_agent_graph._MAX_STEPS_BY_TASK == {"code": 50, "document": 35}
    assert local_agent_graph._max_steps_for("anything-else") == 15


def test_code_toolset_has_git_diff_and_status():
    """Moved out of ide_extra so the coding agent can read its own diff without a subagent."""
    names = {t["function"]["name"] for t in local_agent_tools.get_local_agent_tools("code")}
    assert {"git_diff", "git_status"} <= names


# ------------------------------------------------------------------ guardrails

_IRREVERSIBLE = ["rm -rf /", "mkfs.ext4 /dev/sda", "dd if=/dev/zero of=/dev/sda"]
_NEEDS_CONFIRM = [
    "git commit -m 'x'",
    "git push origin main",
    "git reset --hard HEAD~1",
    "git checkout -b feature",
]
_ALLOWED = ["ls -la", "git status", "git diff HEAD", "pytest -q", "rm -rf ./build"]


@pytest.mark.parametrize("cmd", _IRREVERSIBLE)
def test_irreversible_commands_are_denied_outright(cmd):
    assert registry._denylist_violation("bash_exec", {"command": cmd}) is not None


@pytest.mark.parametrize("cmd", _NEEDS_CONFIRM)
def test_git_mutations_require_confirmation_not_denial(cmd):
    """Regression guard: these used to be hard-denied. They must now block on a human,
    not reject — and must NOT be in the denylist."""
    assert registry._denylist_violation("bash_exec", {"command": cmd}) is None
    assert registry._confirm_required_command("bash_exec", {"command": cmd}) == cmd


@pytest.mark.parametrize("cmd", _ALLOWED)
def test_safe_commands_pass_both_gates(cmd):
    assert registry._denylist_violation("bash_exec", {"command": cmd}) is None
    assert registry._confirm_required_command("bash_exec", {"command": cmd}) is None


def test_github_write_tools_require_confirmation_by_name():
    """These take structured args, not a command string, so they're gated by tool name."""
    for name in ("github_create_issue", "github_create_pr"):
        assert registry._confirm_required_command(name, {"title": "t"}) is not None


def test_no_caller_bypasses_the_guardrails_via_the_raw_executor_map():
    """Both guards live in execute_tool(), not in EXECUTORS. Any code doing
    `EXECUTORS["x"](args)` skips the denylist and the confirmation gate entirely.

    Today the only such callers invoke send_email (ungated, so harmless). This pins that:
    the moment a background job reaches for a gated tool this way, it fails here rather
    than silently executing an unconfirmed `git push` from the task worker.
    """
    gated = set(registry._CONFIRM_REQUIRED_TOOL_NAMES) | {"bash_exec", "python_repl"}
    hits = subprocess.run(
        ["grep", "-rn", "--include=*.py", r"EXECUTORS\[", str(_HARNESS)],
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    offenders = [
        ln
        for ln in hits
        if "registry.py" not in ln
        and "evals/" not in ln
        and "=" not in ln.split("EXECUTORS[")[1][:20]  # assignment, not a call
        and any(f'EXECUTORS["{t}"]' in ln for t in gated)
    ]
    assert not offenders, "guarded tool invoked through the raw executor map:\n" + "\n".join(offenders)


# ------------------------------------------------------------------ mobile tool strip

def test_mobile_strip_does_not_touch_automation_tools():
    """CLAUDE.md: the strip guard fires only on git/repl/filesystem/ide intents, so
    automation CRUD stays reachable from Telegram/WhatsApp."""
    src = (_HARNESS / "harness.py").read_text()
    m = re.search(r'intent in \(("git",\s*"repl",\s*"filesystem",\s*"ide")\)', src)
    assert m, "mobile strip guard's intent tuple changed shape — re-read harness.py"
    assert "automation" not in m.group(1)


def test_mobile_strip_is_gated_on_manual_pick_not_model_presence():
    """The bug: Telegram passes a concrete model too, so gating on `model is not None`
    silently stripped fs/git tools from every mobile message."""
    src = (_HARNESS / "harness.py").read_text()
    assert "_manual_web = manual_model_pick and _chat_mode" in src


# ------------------------------------------------------------------ cost guard

def test_budget_raises_only_at_the_cap(monkeypatch):
    monkeypatch.setattr(cost_guard, "DAILY_TOKEN_BUDGET", 100)

    monkeypatch.setattr(cost_guard, "today_usage", lambda: {"prompt_tokens": 60, "completion_tokens": 39})
    cost_guard.check_budget()  # 99 < 100 — under budget, must not raise

    monkeypatch.setattr(cost_guard, "today_usage", lambda: {"prompt_tokens": 60, "completion_tokens": 41})
    with pytest.raises(cost_guard.BudgetExceededError):
        cost_guard.check_budget()


def test_budget_default_is_the_stale_1m_when_env_absent():
    """Documents a real trap: a standalone script importing cost_guard without
    load_dotenv() sees 1T, not whatever .env says."""
    src = (_HARNESS / "clients/cost_guard.py").read_text()
    assert 'os.environ.get("CLOUD_DAILY_TOKEN_BUDGET", 1_000_000_000_000)' in src


# ------------------------------------------------------------------ misc claims

def test_summarizer_is_cloud_primary_with_local_fallback():
    """_summarize() calls the cloud model first and only falls back to the local
    default (ollama_client.DEFAULT_MODEL, env-overridable via OLLAMA_DEFAULT_MODEL)
    on exception. This matters because every latency/quality number attributed to
    "the local summarizer" was measured on the cloud model."""
    src = inspect.getsource(__import__("tools.websearch", fromlist=["_summarize"])._summarize)
    cloud_at = src.index("DEFAULT_CLOUD_MODEL")
    fallback_at = src.rindex("_LOCAL_DEFAULT")  # usage site, not the import
    assert cloud_at < fallback_at, "summarizer no longer tries cloud first"
    assert "except" in src[:fallback_at], "local model is no longer the fallback path"


def test_browser_wait_never_raises():
    """CLAUDE.md: it swallows every exception into an error string. Callers check the
    return value, so a raise would propagate somewhere nothing catches it."""
    from tools import browser

    src = inspect.getsource(browser.browser_wait)
    assert "except" in src and "raise" not in src.split("except", 1)[1]


def test_local_agent_graph_uses_async_invoke():
    """call_model is `async def` (per-node timeouts). A plain .invoke() anywhere raises
    'No synchronous function provided to "agent"' at runtime."""
    src = (_HARNESS / "agents/local_agent_graph.py").read_text()
    assert "ainvoke" in src
    assert not re.search(r"\bgraph\.invoke\(", src), "sync .invoke() on the graph"


def test_telegram_kokoro_defaults_to_cpu_provider():
    """onnxruntime CoreML is unstable on Apple Silicon — it crashed Telegram TTS under
    concurrency (malloc_zone_error). Don't revert without a concurrency load test."""
    src = (_HARNESS / "telegram_bot.py").read_text()
    assert 'setdefault("ONNX_PROVIDER", "CPUExecutionProvider")' in src


@pytest.mark.xfail(
    strict=True,
    reason="FOUND BY EVAL: chat_ui._get_kokoro() still *prefers* CoreMLExecutionProvider "
    "when available. Only telegram_bot.py got the CPU default. CLAUDE.md says "
    "'_get_kokoro() now defaults CPUExecutionProvider' — true for one of the two.",
)
def test_web_ui_kokoro_defaults_to_cpu_provider():
    """Same crash class as Telegram's: chat_ui serves TTS concurrently too."""
    src = (_HARNESS / "chat_ui.py").read_text()
    fn = src[src.index("def _get_kokoro"):]
    fn = fn[: fn.index("\ndef ", 1)]
    code = "\n".join(ln for ln in fn.splitlines() if not ln.strip().startswith("#"))
    assert "CoreMLExecutionProvider" not in code
