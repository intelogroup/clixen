"""
Persistent cross-session memory: remember → recall → forget.

Uses a temp LanceDB path (never the live store — respects the CLAUDE.md file-lock rule).
Requires Ollama + nomic-embed-text running locally (the embed backend).
"""
import pytest

import tools.memory_tools as mem
from store.knowledge_base import KnowledgeBase


@pytest.fixture
def temp_mem(tmp_path, monkeypatch):
    """Point the memory singleton at a throwaway DB for the duration of the test."""
    kb = KnowledgeBase(db_path=str(tmp_path / "mem.lance"))
    monkeypatch.setattr(mem, "_mem_kb", kb)
    return kb


def test_remember_then_recall(temp_mem):
    mem.remember("The user's favorite editor is helix.")
    block = mem.recall_block("what editor do I like?")
    assert "helix" in block.lower()
    assert block.startswith("## What you remember")


def test_forget_removes_memory(temp_mem):
    mem.remember("The user's favorite editor is helix.")
    assert "helix" in mem.recall_block("favorite editor").lower()

    mem.forget("favorite editor")
    assert mem.recall_block("favorite editor") == ""


def test_empty_namespace_recalls_nothing(temp_mem):
    assert mem.recall_block("anything at all") == ""


def test_unrelated_query_does_not_surface_fact(temp_mem):
    # Distinct, unrelated facts; an off-topic query should rank none close enough to be useful.
    mem.remember("The user's dog is named Zoe.")
    block = mem.recall_block("explain how TCP congestion control works")
    # Semantic store may still return the only row, but it must never invent the wrong content.
    assert "tcp" not in block.lower()


# --- Assumption regression tests (falsified once, now invariants) ---

@pytest.fixture
def seeded_mem(temp_mem):
    for f in [
        "The user's favorite code editor is Helix.",
        "The user lives in Brussels, Belgium.",
        "The user's primary programming language is Python.",
        "The user has a dog named Zoe.",
        "The user prefers concise lazy-senior code style.",
    ]:
        mem.remember(f)
    return temp_mem


def test_irrelevant_query_injects_nothing(seeded_mem):
    # A2: trivial/off-topic turns must NOT flood the prompt with all memories.
    assert mem.recall_block("hi") == ""
    assert mem.recall_block("what is 2+2") == ""
    assert mem.recall_block("explain TCP congestion control") == ""


def test_relevant_query_still_recalls(seeded_mem):
    # Threshold must not be so tight it suppresses genuine matches.
    assert "helix" in mem.recall_block("what text editor do I use").lower()
    assert "brussels" in mem.recall_block("where do I live").lower()


def test_forget_no_collateral_damage(seeded_mem):
    # A3: forget must delete only the targeted memory, not its neighbors.
    before = seeded_mem.table.count_rows()
    mem.forget("favorite code editor")
    after = seeded_mem.table.count_rows()
    assert before - after == 1
    assert mem.recall_block("text editor") == ""
    # neighbors survive
    assert "brussels" in mem.recall_block("where do I live").lower()


def test_remember_is_idempotent(seeded_mem):
    # A4: storing an identical fact must not create a duplicate row.
    n0 = seeded_mem.table.count_rows()
    mem.remember("The user lives in Brussels, Belgium.")
    assert seeded_mem.table.count_rows() == n0


def test_search_sessions_finds_remembered_facts(temp_mem):
    # search_sessions() originally only searched the fold-summary source
    # (_SESSION_SOURCE) and never the source remember() writes to (_SOURCE) — so
    # it came back empty for a fact that was actually stored, just under the
    # other source (confirmed live 2026-07-13).
    mem.remember("The user's favorite editor is helix.")
    result = mem.search_sessions("what editor do I like?")
    assert "helix" in result.lower()


def test_search_sessions_still_finds_fold_summaries(temp_mem):
    mem.save_session_summary("chat123", "Discussed deploying the new auth service.")
    result = mem.search_sessions("what did we discuss about deploying")
    assert "auth service" in result.lower()
    assert "chat123" in result


def test_recall_block_surfaces_fold_summary(temp_mem):
    """Fold summaries (session_memory source) are now auto-recalled per turn,
    not just via explicit search_sessions — facts that slid out of a chat window
    surface in the prompt block as labeled 'earlier conversation' context."""
    mem.save_session_summary("chat456", "user prefers dark roast coffee")
    block = mem.recall_block("what coffee do they like?")
    assert "dark roast" in block.lower()
    assert "earlier conversation" in block
    assert block.startswith("## What you remember")
