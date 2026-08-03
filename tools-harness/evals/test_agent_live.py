"""Tier 2 — live agent evals. Real models, real tools, real cost. `make eval-live`.

These are the assumptions no offline test can settle: whether a model actually calls a tool,
whether a chain survives to step 3, whether think=False is really load-bearing. Every grader
here reads the *outcome* — a file on disk, a parsed PDF, an absent commit — not the sequence
of tool calls the agent chose to get there. Agents find valid paths we didn't anticipate;
grading the path punishes them for it.

Each task runs k times because agents are stochastic. One green run is not evidence.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

_K = 3  # trials per task


def _run_agent(query: str, model: str, task: str, chat_id: str) -> str:
    """Drive the real graph, return the concatenated assistant text."""
    from agents.local_agent_graph import run_local_agent_streaming

    out = []
    for event_type, data in run_local_agent_streaming(
        query=query, model=model, chat_id=chat_id, task=task
    ):
        if event_type == "token":
            out.append(data.get("text", ""))
    return "".join(out)


# ------------------------------------------------------------------ capability floor

# CLAUDE.md: "~7B minimum for agentic chains"; granite3.3:2b / phi4-mini narrate instead of
# calling tools. This eval is what makes that number a measurement rather than a folk belief.
_FLOOR_MODELS = ["granite3.3:2b", "gemma4:e2b", "gemma4:12b-mlx"]


@pytest.mark.parametrize("model", _FLOOR_MODELS)
def test_tool_chaining_capability_floor(model, clean_env, score):
    """A 3-step filesystem chain. Outcome: the final file exists with the right content.

    Grading the outcome, not the calls: an agent that gets there in two steps passes.
    """
    score.k = _K
    score.name = f"capability_floor[{model}]"

    for trial in range(_K):
        target = clean_env / f"out_{trial}.txt"
        src = clean_env / f"in_{trial}.txt"
        src.write_text("alpha\nbeta\ngamma\n")

        try:
            _run_agent(
                f"Read {src}, then write only the second line of it into {target}.",
                model=model,
                task="code",
                chat_id=f"eval-floor-{model}-{trial}",
            )
        except Exception as exc:  # a crash is a failed trial, not a failed eval
            score.record(model, False, f"raised {type(exc).__name__}")
            continue

        ok = target.exists() and target.read_text().strip() == "beta"
        got = target.read_text().strip()[:30] if target.exists() else "<no file>"
        score.record(model, ok, f"got {got!r}")

    # Sub-7B models are *expected* to fail; the eval records the floor rather than asserting it.
    # pass_rate, not pass_at_1: one task_id with k trials — pass_at_1 only reads trial 0.
    if model == "gemma4:12b-mlx":
        assert score.pass_rate >= 0.66, f"the primary local model fell to {score.pass_rate:.0%}"


# ------------------------------------------------------------------ think=False

def _raw_chat(prompt: str, think: bool | None, tools=None, num_predict: int = 300):
    """Talk to Ollama the way ollama_client does, but with `think` under our control —
    the client hardcodes think=False for gemma4/qwen3, which is the very claim under test."""
    import ollama

    return ollama.chat(
        model="gemma4:12b-mlx",
        messages=[{"role": "user", "content": prompt}],
        tools=tools,
        think=think,
        options={"num_predict": num_predict},
    )


def test_think_false_is_required_for_tool_calling(score):
    """CLAUDE.md: gemma4 defaults to thinking-on, and with a low num_predict that yields
    empty responses / no tool call. If think=True now matches think=False here, the
    hardcoded think=False in ollama_client.py is stale and can be dropped."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from disk",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    ]
    score.k = _K
    rates = {}
    for think in (False, True):
        hits = 0
        for _ in range(_K):
            try:
                resp = _raw_chat("Read the file /tmp/notes.txt and tell me line one.", think, tools)
                called = bool(resp["message"].get("tool_calls"))
            except Exception:
                called = False
            hits += called
            score.record(f"think={think}", called)
        rates[think] = hits / _K

    print(f"\n  tool-call rate — think=False: {rates[False]:.0%}   think=True: {rates[True]:.0%}")
    assert rates[False] >= rates[True], (
        "think=True now matches or beats think=False on tool calling — "
        "the hardcoded think=False in ollama_client.py may be stale"
    )


def test_think_false_degrades_summarization(score):
    """The other half of the same claim, and the reason websearch never sets think=False:
    on synthesis (no tools, just prose) it produces empty or raw-thinking output."""
    evidence = "The Eiffel Tower is 330m tall. It was completed in 1889. It stands in Paris."
    prompt = f"Summarize in one sentence: {evidence}"

    score.k = _K
    degraded_false = degraded_true = 0
    for _ in range(_K):
        for think, bucket in ((False, "think=False"), (True, "think=True")):
            try:
                text = (_raw_chat(prompt, think)["message"]["content"] or "").strip()
            except Exception:
                text = ""
            degraded = not text or "<think>" in text
            score.record(bucket, not degraded, f"got {text[:40]!r}")
            if think:
                degraded_true += degraded
            else:
                degraded_false += degraded

    print(f"\n  degraded — think=False: {degraded_false}/{_K}   think=True: {degraded_true}/{_K}")
    assert degraded_false >= degraded_true, (
        "think=False no longer degrades summarization — websearch could use it and get faster"
    )


# ------------------------------------------------------------------ undo

def test_undo_reverts_the_most_recent_edit_only(clean_env):
    """Deterministic, but needs the real tool. _UNDO_SNAPSHOTS is one slot per path,
    last-write-wins — the second edit's undo must not restore the original."""
    from tools.shell import edit_file, undo_last_edit

    f = clean_env / "code.py"
    f.write_text("a = 1\nb = 2\n")

    edit_file(str(f), "a = 1", "a = 99")
    edit_file(str(f), "b = 2", "b = 88")
    undo_last_edit(str(f))

    assert f.read_text() == "a = 99\nb = 2\n", "undo restored the wrong snapshot"

    # One slot: a second undo has nothing left to restore.
    again = undo_last_edit(str(f))
    assert f.read_text() == "a = 99\nb = 2\n"
    assert "no" in again.lower() or "error" in again.lower()


# ------------------------------------------------------------------ confirmation gate

def test_destructive_bash_blocks_and_leaves_no_commit(clean_env):
    """Outcome grader, not a mock: after the agent tries `git commit`, the repo must have
    no new commit and a confirmation must be pending."""
    from tools import confirmation
    from tools.registry import execute_tool  # the guards live here, NOT in EXECUTORS

    subprocess.run(["git", "init", "-q"], cwd=clean_env, check=True)
    (clean_env / "f.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=clean_env, check=True)

    before = subprocess.run(
        ["git", "rev-list", "--count", "--all"], cwd=clean_env, capture_output=True, text=True
    ).stdout.strip()

    import threading

    result = {}
    t = threading.Thread(
        target=lambda: result.update(out=execute_tool("bash_exec", {"command": "git commit -m eval"})),
        daemon=True,
    )
    t.start()
    t.join(timeout=3)  # must still be blocked on the confirmation event
    assert t.is_alive(), f"execute_tool returned instead of blocking: {result.get('out')!r}"

    pending = confirmation.list_pending()
    assert pending, "destructive command ran without requesting confirmation"
    assert any("git commit" in p["command"] for p in pending)

    after = subprocess.run(
        ["git", "rev-list", "--count", "--all"], cwd=clean_env, capture_output=True, text=True
    ).stdout.strip()
    assert after == before, "the commit landed while awaiting confirmation"

    for p in pending:
        confirmation.resolve(p["token"], approved=False)


# ------------------------------------------------------------------ summarizer brevity

_FACT_QUERIES = [
    "what is the capital of Australia",
    "how tall is the Eiffel Tower",
    "who wrote the Odyssey",
    "what year did the Berlin Wall fall",
    "what is the boiling point of water in fahrenheit",
]


def test_summarizer_answers_simple_facts_in_under_80_words(score):
    """CLAUDE.md pins the default format at 1-3 sentences / <80 words (was ~200-word
    answers). Regression here is a silent 5x latency and token cost."""
    from tools.websearch import search

    score.name = "summarizer_brevity"
    for q in _FACT_QUERIES:
        answer = search(q)
        words = len(answer.split())
        sentences = len([s for s in re.split(r"[.!?]+", answer) if s.strip()])
        ok = words < 80 and sentences <= 3
        score.record(q, ok, f"{words}w / {sentences}s")

    assert score.pass_at_1 >= 0.8, "summarizer got verbose again"


# ------------------------------------------------------------------ form chain

def test_form_fill_chain_produces_a_filled_pdf(clean_env, score):
    """The detect -> fill -> detect chain. Outcome: the filled PDF actually contains the
    value. An agent that says "I filled the form" without a _filled.pdf on disk fails."""
    fixture = Path(__file__).parent / "fixtures" / "simple_form.pdf"
    if not fixture.exists():
        pytest.skip("no fixture PDF — drop a fillable form at evals/fixtures/simple_form.pdf")

    score.k = _K
    for trial in range(_K):
        work = clean_env / f"form_{trial}.pdf"
        work.write_bytes(fixture.read_bytes())
        _run_agent(
            f"Fill {work}: set the Name field to Jim Kalinov.",
            model="gemma4:12b-mlx",
            task="document",
            chat_id=f"eval-form-{trial}",
        )
        filled = work.with_name(work.stem + "_filled.pdf")
        ok = False
        if filled.exists():
            from tools.form_tools import detect_form_fields

            ok = "Jim Kalinov" in str(detect_form_fields(str(filled)))
        score.record("form_fill", ok, "no _filled.pdf" if not filled.exists() else "value missing")

    # pass_rate: single task_id, k trials — see capability-floor note.
    assert score.pass_rate >= 0.66
