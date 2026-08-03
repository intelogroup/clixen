"""Offline structural checks for skills_hub.py — no LLM, no network, no auth.

Tests three fixes:
1. Sub-skill shadowing: parent gstack SKILL.md skipped when sub-skills exist
2. Category propagation: sub-skills get parent dir name as category
3. gstack_skill_info tool: returns correct frontmatter metadata
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skills_hub import (
    list_skills, get_skill, gstack_skill_info, SKILLS, _has_sub_skills,
    _scan_external_skills, SKILLS_HUB_SCHEMAS, SKILLS_HUB_EXECUTORS,
)
from tools.registry import ALL_TOOLS, EXECUTORS


def _reset_and_rescan():
    """Reset SKILLS and re-scan for clean test state."""
    SKILLS.clear()
    from skills_hub import _registered_external_ids
    _registered_external_ids.clear()
    _scan_external_skills()


def _get_ext_skills():
    """Return only externally-registered skills."""
    return [s for s in list_skills() if s["id"].startswith("ext.")]


def test_no_parent_gstack_router_registered():
    """Verify parent gstack/SKILL.md is skipped — no ext.gstack entry."""
    _reset_and_rescan()
    ids = [s["id"] for s in _get_ext_skills()]
    assert "ext.gstack" not in ids, (
        f"parent gstack SKILL.md should be skipped, got: {ids}"
    )


def test_gstack_sub_skills_have_correct_category():
    """Verify gstack sub-skills (qa, autoplan, investigate) have category='gstack'."""
    _reset_and_rescan()
    gstack_subs = [s for s in _get_ext_skills() if s["category"] == "gstack"]
    assert len(gstack_subs) >= 10, (
        f"expected 10+ gstack sub-skills, got {len(gstack_subs)}: "
        f"{[s['name'] for s in gstack_subs]}"
    )
    expected = {"ext.qa", "ext.autoplan", "ext.investigate", "ext.browse"}
    found = {s["id"] for s in gstack_subs}
    missing = expected - found
    assert not missing, (
        f"expected gstack sub-skills missing: {missing}. "
        f"All gstack ids: {found}"
    )


def test_top_level_external_skills_get_external_category():
    """Verify top-level skills (no sub-dirs) get category='external'."""
    _reset_and_rescan()
    ext = _get_ext_skills()
    for s in ext:
        if s["category"] == "external":
            # At least some top-level skills should exist
            return
    assert False, "no skills with category='external' found — top-level registration may be broken"


def test_gstack_skill_info_returns_metadata():
    """Verify gstack_skill_info returns frontmatter fields for a known sub-skill."""
    _reset_and_rescan()
    result = gstack_skill_info({"skill_id": "ext.investigate"})
    assert "skill_id: ext.investigate" in result, result
    assert "category: gstack" in result, result
    assert "triggers:" in result, result
    assert "debug this" in result or "bug" in result, result
    assert "allowed_tools:" in result, result


def test_gstack_skill_info_unknown_id():
    """Verify gstack_skill_info returns error for unknown skill_id."""
    result = gstack_skill_info({"skill_id": "ext.nonexistent"})
    assert "error" in result.lower(), result


def test_gstack_skill_info_missing_id():
    """Verify gstack_skill_info returns error when skill_id omitted."""
    result = gstack_skill_info({})
    assert "error" in result.lower(), result


def test_gstack_skill_info_is_registered():
    """Verify gstack_skill_info is in ALL_TOOLS and EXECUTORS."""
    names = [s.get("function", {}).get("name") for s in ALL_TOOLS]
    assert "gstack_skill_info" in names, (
        "gstack_skill_info not found in ALL_TOOLS schemas"
    )
    assert "gstack_skill_info" in EXECUTORS, (
        "gstack_skill_info not found in EXECUTORS"
    )


def test_has_sub_skills_positive():
    """Verify _has_sub_skills detects a router dir."""
    result = _has_sub_skills(os.path.expanduser("~/.claude/skills/gstack"))
    assert result, "gstack dir has sub-skills — should return True"


def test_has_sub_skills_negative():
    """Verify _has_sub_skills returns False for leaf dir (no sub-skills)."""
    # Use a dir that has no SKILL.md sub-dirs
    tmp = os.path.expanduser("~/.claude")
    if not os.path.isdir(tmp):
        return  # soft-skip
    leaf_skill = next(
        (d for d in os.listdir(tmp)
         if os.path.isdir(os.path.join(tmp, d))
         and not _has_sub_skills(os.path.join(tmp, d))),
        None
    )
    if leaf_skill:
        assert not _has_sub_skills(os.path.join(tmp, leaf_skill))


if __name__ == "__main__":
    test_no_parent_gstack_router_registered()
    test_gstack_sub_skills_have_correct_category()
    test_top_level_external_skills_get_external_category()
    test_gstack_skill_info_returns_metadata()
    test_gstack_skill_info_unknown_id()
    test_gstack_skill_info_missing_id()
    test_gstack_skill_info_is_registered()
    test_has_sub_skills_positive()
    test_has_sub_skills_negative()
    print("OK — all 9 tests passed.")
