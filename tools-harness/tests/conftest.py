"""Auto-skip `live` tests when their external deps are unavailable.

A test marked ``@pytest.mark.live`` (or module-level ``pytestmark = pytest.mark.live``) needs a
real Ollama model, external network, external creds, or a running server. With everything up,
``pytest`` runs them. Offline / CI without deps, they auto-skip so the deterministic core stays
green. Explicit selection is respected: ``pytest -m live`` forces them, ``pytest -m "not live"``
skips them regardless of dep state.
"""
import shutil
import socket
import urllib.request

import pytest


@pytest.fixture(autouse=True)
def _reset_run_id_context():
    """harness.run() sets CURRENT_RUN_ID via a plain ContextVar.set() with no reset
    (deliberately, see harness.py's own comment — wrapping its 1000+ line run() in
    try/finally was judged too risky). In-process test runs share that ContextVar
    across the whole pytest session, so a run_id set by one test's harness.run()
    call leaks into every later test — this broke tools/orchestrator_tools.py's
    per-run subagent dedupe cache, which keys off CURRENT_RUN_ID and served a
    stale cached result to unrelated tests using the same intent/query text
    (observed 2026-07-11: test_subagent_envelope.py failures only under full-suite
    ordering, passing in isolation). Reset here instead of touching run().
    """
    from log_config import CURRENT_RUN_ID
    from tools import orchestrator_tools

    token = CURRENT_RUN_ID.set("")
    orchestrator_tools._SUBAGENT_CACHE.clear()
    yield
    CURRENT_RUN_ID.reset(token)
    orchestrator_tools._SUBAGENT_CACHE.clear()


def safe_delete_test_workflow(workflow_id: str) -> None:
    """Delete a workflow_instances row created by a test — refuses if the row
    has a non-empty dedupe_key, which is set ONLY by
    store.workflow_store.seed_builtins() (create_automation()/
    create_workflow() never set it), making dedupe_key!="" a reliable signal
    for "this is a protected builtin, not something a test created."

    Root-cause fix for a 2026-07-11 bug: a test's own id-parsing mistake
    (extracting an id via string-split on a create_automation() return value
    that could itself contain a DIFFERENT automation's id, inside an FYI
    note) deleted the real "Monitor inbox attachments" builtin instead of
    its own test row — three times, each recreated fresh (new id, reset to
    active) by the next seed_builtins() call on core.py restart. This guard
    makes that whole class of mistake loud and non-destructive instead of a
    silent, hard-to-trace production data loss.
    """
    from store import workflow_store
    inst = workflow_store.get_workflow_instance(workflow_id)
    if inst and inst.get("dedupe_key"):
        raise AssertionError(
            f"Refusing to delete {workflow_id!r} — it has a non-empty "
            f"dedupe_key ({inst['dedupe_key']!r}), meaning it's a protected "
            f"builtin automation ({inst['task_name']!r}), not a test row."
        )
    workflow_store.delete_workflow_instance(workflow_id)


def _ollama_up() -> bool:
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.5)
        return True
    except Exception:
        return False


def _network_up() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=1.5).close()
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    # Respect an explicit -m selection — the user asked for exactly those tests.
    if getattr(config.option, "markexpr", ""):
        return
    if _ollama_up() and _network_up() and shutil.which("opencli"):
        return  # deps present — run live tests too
    _missing = []
    if not _ollama_up(): _missing.append("Ollama")
    if not _network_up(): _missing.append("network")
    if not shutil.which("opencli"): _missing.append("opencli")
    skip_live = pytest.mark.skip(
        reason=f"live deps unavailable ({', '.join(_missing)}); bring deps up or run `pytest -m live`"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
