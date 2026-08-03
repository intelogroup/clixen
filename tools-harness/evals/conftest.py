"""Eval harness — three tiers, one runner.

tier 0  test_invariants.py    deterministic, no model, no network. Asserts the code-facts
                              CLAUDE.md claims. Runs in CI.
tier 1  test_routing_offline.py  mocked LLM. Scores the classifier + guards on a labeled
                              dataset (cases.jsonl). Runs in CI.
tier 2  test_agent_live.py    real models, real tools, real cost. `-m live` only.

Design rules (from Anthropic's agent-eval guide):
- Grade OUTCOMES (final state: file contents, DB row, sent message), not trajectories
  (exact tool-call sequences). Trajectory matching is rigid and overfits.
- Deterministic graders wherever possible; LLM judge only where deterministic can't reach.
- Isolate every trial: no shared DBs, no leftover files, no cross-trial cache.
- Agents are stochastic. Report pass@1 and pass^k, not a single boolean.
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HARNESS))


# ---------------------------------------------------------------- dataset

def load_cases(tier: str | None = None, channel: str | None = None) -> list[dict]:
    """Read cases.jsonl, optionally filtered. One JSON object per line."""
    path = Path(__file__).parent / "cases.jsonl"
    rows = [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("//")
    ]
    if tier:
        rows = [r for r in rows if r.get("tier") == tier]
    if channel:
        rows = [r for r in rows if r.get("channel") == channel]
    return rows


# ---------------------------------------------------------------- scoring

@dataclass
class Score:
    """Accumulates trial outcomes for one eval, prints a scoreboard at teardown.

    pass@1  — fraction of tasks passing on their first trial.
    pass^k  — fraction of tasks passing on *every* one of k trials. The bar that
              matters for user-facing paths: a 75%/trial agent is 42% at pass^3.
    """

    name: str
    k: int = 1
    # task_id -> list of per-trial bools
    trials: dict[str, list[bool]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    def record(self, task_id: str, ok: bool, note: str = "") -> None:
        self.trials.setdefault(task_id, []).append(bool(ok))
        if note and not ok:
            self.notes[task_id] = note

    @property
    def pass_at_1(self) -> float:
        if not self.trials:
            return 0.0
        return sum(t[0] for t in self.trials.values()) / len(self.trials)

    @property
    def pass_rate(self) -> float:
        """Mean over every trial of every task. Use this for k-trials-of-one-task evals —
        pass_at_1 only reads trial 0 there."""
        flat = [r for t in self.trials.values() for r in t]
        return sum(flat) / len(flat) if flat else 0.0

    @property
    def pass_pow_k(self) -> float:
        if not self.trials:
            return 0.0
        return sum(all(t) for t in self.trials.values()) / len(self.trials)

    def report(self) -> str:
        lines = [f"\n=== {self.name}  (n={len(self.trials)}, k={self.k}) ==="]
        lines.append(f"  pass@1  {self.pass_at_1:.1%}")
        if self.k > 1:
            lines.append(f"  pass^{self.k}  {self.pass_pow_k:.1%}")
        for task_id, results in sorted(self.trials.items()):
            if not all(results):
                mark = "".join("." if r else "x" for r in results)
                note = self.notes.get(task_id, "")
                lines.append(f"  [{mark}] {task_id}  {note}")
        return "\n".join(lines)


@pytest.fixture
def score(request):
    """Yields a Score; prints the scoreboard after the test regardless of outcome."""
    s = Score(name=request.node.name)
    yield s
    if s.trials:
        print(s.report())


# ---------------------------------------------------------------- isolation

@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """A trial that touches disk state gets its own copy of it.

    Shared state across trials produces correlated failures (infra flakiness read as
    agent regression) and can inflate scores — an agent that finds a previous trial's
    output passes without doing the work.
    """
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(work)

    # Per-trial DBs — never point an eval at the live store. Both modules hardcode
    # DB_PATH relative to their own file; there is no env override, so patch the constant.
    import jobs.job_queue as job_queue
    import store.workflow_store as workflow_store

    monkeypatch.setattr(job_queue, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(workflow_store, "DB_PATH", tmp_path / "workflow_state.db")

    # edit_file's undo cache is a module global; a leftover snapshot leaks across trials
    import tools.shell as shell

    monkeypatch.setattr(shell, "_UNDO_SNAPSHOTS", {})

    yield work

    shutil.rmtree(work, ignore_errors=True)


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly instead of silently reaching the internet in an offline eval."""
    import socket

    def _deny(*a, **k):
        raise AssertionError("offline eval attempted a network connection")

    monkeypatch.setattr(socket, "socket", _deny)


# ---------------------------------------------------------------- live gate

def pytest_collection_modifyitems(config, items):
    """`live` is skipped unless explicitly selected with -m live — tier 2 costs real tokens."""
    if getattr(config.option, "markexpr", ""):
        return
    for item in items:
        if "live" in item.keywords:
            item.add_marker(
                pytest.mark.skip(reason="tier-2 eval: costs tokens; run `make eval-live`")
            )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: tier-2 eval, real models, real cost")
