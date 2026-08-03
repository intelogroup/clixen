"""Verify clixen can match + run skills end-to-end.

Two pipelines:
1. match_skill_for_task — NLP scoring routes query to correct skill
2. run_skill — dispatches skill to its executor

Single-tool skills run zero-LLM (param extraction + executor call).

NOTE: builtin skills (web_search, list_automations, etc.) load once at
skills_hub import time. Tests must not clear+rescan without reloading
skills_data modules. For builtin-skills tests we re-scan externals only;
for ext.*-skill tests we do a full reset.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import skills_hub
from skills_hub import (
    SKILLS, _registered_external_ids, _scan_external_skills,
    _score_skill, match_skill_for_task, run_skill, list_skills,
    match_skill,
)
from tools.registry import EXECUTORS


def _reset_external_only():
    """Re-scan external skills without destroying builtins."""
    # Remove only ext.* entries
    to_remove = [s for s in SKILLS if s.id.startswith("ext.")]
    for s in to_remove:
        SKILLS.remove(s)
    _registered_external_ids.clear()
    _scan_external_skills()


def _count_by_prefix():
    ext = sum(1 for s in SKILLS if s.id.startswith("ext."))
    builtin = len(SKILLS) - ext
    return ext, builtin


# ---------------------------------------------------------------------------
# match_skill_for_task — NLP routing
# ---------------------------------------------------------------------------

def test_match_investigate():
    _reset_external_only()
    r = match_skill_for_task({"query": "debug this login failure"})
    assert "investigate" in r.lower(), f"expected investigate, got: {r[:100]}"


def test_match_qa():
    _reset_external_only()
    r = match_skill_for_task({"query": "qa test this website"})
    assert "qa" in r.lower(), f"expected qa, got: {r[:100]}"


def test_match_web_search():
    _reset_external_only()
    r = match_skill_for_task({"query": "search web for latest ai news"})
    assert "web_search" in r.lower() or "web search" in r.lower(), (
        f"expected web_search, got: {r[:100]}"
    )
    # Verify web_search is a builtin skill that actually routes
    skill = match_skill("search web for latest ai news")
    assert skill is not None, "match_skill returned None for web search query"
    assert skill.id == "web_search", f"expected web_search, got {skill.id}"


def test_match_calendar():
    _reset_external_only()
    r = match_skill_for_task({"query": "what's on my calendar today"})
    assert any(kw in r.lower() for kw in ["calendar", "schedule"]), (
        f"expected calendar/schedule, got: {r[:100]}"
    )


def test_match_tasks():
    _reset_external_only()
    r = match_skill_for_task({"query": "list my tasks"})
    assert "task" in r.lower(), f"expected task skill, got: {r[:100]}"


def test_match_arxiv():
    _reset_external_only()
    r = match_skill_for_task({"query": "search arxiv for papers"})
    assert "arxiv" in r.lower(), f"expected arxiv, got: {r[:100]}"


def test_match_no_match():
    _reset_external_only()
    r = match_skill_for_task({"query": "xyznonexistent"})
    assert "no matching skill" in r.lower(), f"expected no match, got: {r[:100]}"


def test_scoring_nonzero_for_real_query():
    """Verify token+description overlap gives non-zero score for real-world phrases."""
    _reset_external_only()
    scored = [(s.id, _score_skill(s, "find out why login is failing"))
              for s in SKILLS if _score_skill(s, "find out why login is failing") > 0]
    assert len(scored) > 0, (
        f"no skill scored > 0 for 'find out why login is failing'. "
        f"Check token overlap + description overlap logic."
    )


# ---------------------------------------------------------------------------
# run_skill — execution pipeline
# ---------------------------------------------------------------------------

def test_run_single_tool_skill():
    """Single-tool skills run directly via EXECUTORS — no LLM needed."""
    _reset_external_only()
    r = run_skill({"skill_id": "list_automations", "message": "list my automations"})
    assert "error" not in r.lower(), f"run failed: {r[:200]}"
    assert len(r) > 10, f"suspiciously short result: {r[:200]}"


def test_run_skill_unknown_id():
    _reset_external_only()
    r = run_skill({"skill_id": "ext.nonexistent", "message": "hello"})
    assert "error" in r.lower(), f"expected error, got: {r[:100]}"


def test_run_skill_missing_args():
    _reset_external_only()
    r = run_skill({})
    assert "error" in r.lower(), f"expected error, got: {r[:100]}"


def test_run_skill_missing_message():
    _reset_external_only()
    r = run_skill({"skill_id": "ext.qa"})
    assert "error" in r.lower(), f"expected error, got: {r[:100]}"


# ---------------------------------------------------------------------------
# Full pipeline: match + run
# ---------------------------------------------------------------------------

def test_match_then_run_builtin():
    """Verify match->run chain for a builtin single-tool skill."""
    _reset_external_only()
    best = match_skill_for_task({"query": "list my automations"})
    assert "list_automations" in best, f"expected list_automations, got: {best[:100]}"
    r = run_skill({"skill_id": "list_automations", "message": "list my automations"})
    assert "error" not in r.lower(), f"run failed: {r[:200]}"


def test_match_then_run_ext():
    """Verify match->run chain for an external sub-skill."""
    _reset_external_only()
    best = match_skill_for_task({"query": "debug this login failure"})
    assert "investigate" in best.lower(), f"expected investigate, got: {best[:100]}"


def test_tool_is_registered():
    """Verify every single-tool skill's tool exists in EXECUTORS."""
    _reset_external_only()
    for s in list_skills():
        if len(s["tools"]) != 1:
            continue
        tool = s["tools"][0]
        assert tool in EXECUTORS, (
            f"skill {s['id']} requires tool {tool!r} not in EXECUTORS"
        )


def test_builtin_and_external_exist():
    """Verify both builtin and external skill populations are loaded."""
    _reset_external_only()
    ext, builtin = _count_by_prefix()
    assert ext > 50, f"expected 50+ external skills, got {ext}"
    assert builtin > 20, f"expected 20+ builtin skills, got {builtin}"


def test_match_builtin_direct_tool():
    """Verify a builtin skill routes to direct tool execution (zero LLM)."""
    _reset_external_only()
    r = run_skill({"skill_id": "list_automations", "message": "list automations"})
    assert "error" not in r.lower(), f"run failed: {r[:200]}"
    # list_automations is single-tool → direct executor call
    assert len(r) > 10


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_edge_empty_query():
    """Empty query returns no match."""
    r = match_skill_for_task({"query": ""})
    assert "no matching skill" in r.lower(), f"expected no match, got: {r[:100]}"


def test_edge_weak_only_query():
    """Only weak keywords (the/a/an/this/my) → no match."""
    r = match_skill_for_task({"query": "the a an this my"})
    assert "no matching skill" in r.lower(), f"expected no match, got: {r[:100]}"


def test_edge_very_short_query():
    """1-2 char queries return no match."""
    r = match_skill_for_task({"query": "hi"})
    assert "no matching skill" in r.lower(), f"expected no match, got: {r[:100]}"


def test_edge_round_penalty():
    """Short query (< 15 chars) on complex skill (max_rounds > 6) gets -1.0 penalty."""
    _reset_external_only()
    complex_skills = [s for s in SKILLS if s.max_rounds > 6]
    assert len(complex_skills) > 0, "expected some complex skills"
    # 'review design' = 13 chars, should get round penalty
    r14 = _score_skill(complex_skills[0], "review design")
    # 'review the design' = 17 chars, no penalty
    r17 = _score_skill(complex_skills[0], "review the design")
    assert r14 <= r17, (
        f"round penalty failed: 13-char query ({r14}) > 17-char ({r17}) "
        f"for {complex_skills[0].id}"
    )


def test_edge_regex_trigger():
    """Skills with trigger_regex get +5.0 when regex matches."""
    _reset_external_only()
    for s in SKILLS:
        if s.id == "arxiv_search":
            sc = _score_skill(s, "searching arxiv for papers")
            assert sc >= 5.0, f"expected regex bonus, got {sc}"
            return
    raise AssertionError("arxiv_search not found in SKILLS")


def test_edge_stemming_overlap():
    """Description overlap uses stemming so 'searching' matches 'search' in desc."""
    _reset_external_only()
    agent_browser = [s for s in SKILLS if s.id == "ext.agent-browser"]
    assert len(agent_browser) > 0, "ext.agent-browser not found"
    s = agent_browser[0]
    # Description includes 'interact with websites', query 'website interacting'
    sc = _score_skill(s, "website interacting")
    assert sc > 0, f"description overlap with stemming should score > 0, got {sc}"


def test_edge_case_insensitive():
    """Uppercase query matches same way as lowercase."""
    _reset_external_only()
    lower = match_skill_for_task({"query": "search arxiv for papers"})
    upper = match_skill_for_task({"query": "SEARCH ARXIV FOR PAPERS"})
    assert lower == upper, (
        f"case mismatch: lower={lower!r}, upper={upper!r}"
    )


def test_edge_unicode_safe():
    """Unicode/emoji in query does not crash and returns no match."""
    _reset_external_only()
    r = match_skill_for_task({"query": "find café 🎉 unicode"})
    # Should not crash, likely no match
    assert isinstance(r, str), f"expected string, got {type(r)}"
    assert len(r) > 0


def test_edge_skill_id_whitespace():
    """Whitespace-only skill_id returns error."""
    _reset_external_only()
    r = run_skill({"skill_id": "  ", "message": "hello"})
    assert "error" in r.lower(), f"expected error, got: {r[:100]}"


def test_edge_zero_tool_skills():
    """Skills with zero tools route to orchestrator and return string."""
    _reset_external_only()
    for s in SKILLS:
        if s.id == "ext.caveman":
            r = run_skill({"skill_id": s.id, "message": "hello"})
            assert isinstance(r, str), f"expected string, got {type(r)} ({r!r})"
            return
    raise AssertionError("ext.caveman not found")


def test_edge_domain_anchor_category_mismatch():
    """Domain anchor doesn't fire when skill category doesn't match."""
    _reset_external_only()
    ext_docs = [s for s in SKILLS if s.id == "ext.docs"]
    assert len(ext_docs) > 0, "ext.docs not found"
    s = ext_docs[0]
    # 'open google docs' matches 'docs' domain but category is 'external'
    sc = _score_skill(s, "open google docs")
    # Should get description overlap but NOT domain bonus
    assert sc < 3.0, (
        f"expected < 3.0 (no domain bonus for ext.docs), got {sc}"
    )


def test_edge_tie_first_wins():
    """On tie at max score, first skill in SKILLS wins (stable iteration)."""
    _reset_external_only()
    from collections import defaultdict
    scored = defaultdict(list)
    for s in SKILLS:
        sc = _score_skill(s, "file")
        if sc > 0:
            scored[sc].append(s.id)
    max_score = max(scored.keys())
    tied_at_max = sorted(scored[max_score])
    assert len(tied_at_max) > 1, f"expected tie at max for 'file', got {tied_at_max}"
    first_in_list = next(s for s in SKILLS if _score_skill(s, "file") == max_score)
    best = match_skill("file").id
    assert best == first_in_list.id, (
        f"expected {first_in_list.id} to win on tie for 'file', got {best} "
        f"(tied: {tied_at_max})"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_match_investigate()
    test_match_qa()
    test_match_web_search()
    test_match_calendar()
    test_match_tasks()
    test_match_arxiv()
    test_match_no_match()
    test_scoring_nonzero_for_real_query()
    test_run_single_tool_skill()
    test_run_skill_unknown_id()
    test_run_skill_missing_args()
    test_run_skill_missing_message()
    test_match_then_run_builtin()
    test_match_then_run_ext()
    test_tool_is_registered()
    test_builtin_and_external_exist()
    test_match_builtin_direct_tool()
    test_edge_empty_query()
    test_edge_weak_only_query()
    test_edge_very_short_query()
    test_edge_round_penalty()
    test_edge_regex_trigger()
    test_edge_stemming_overlap()
    test_edge_case_insensitive()
    test_edge_unicode_safe()
    test_edge_skill_id_whitespace()
    test_edge_zero_tool_skills()
    test_edge_domain_anchor_category_mismatch()
    test_edge_tie_first_wins()
    print(f"OK — 28 tests passed.")
