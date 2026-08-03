"""E2E test: clixen reads skills from user's computer.

Verifies actual gstack sub-skills on disk are registered, categorized,
and their frontmatter metadata is readable via gstack_skill_info.

Skips: no LLM, no network, no auth. Reads raw SKILL.md files only.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skills_hub import (
    SKILLS, _registered_external_ids, _scan_external_skills,
    list_skills, get_skill, gstack_skill_info, _EXTERNAL_SKILL_ROOTS,
)


def _reset():
    SKILLS.clear()
    _registered_external_ids.clear()
    _scan_external_skills()


def test_skills_load_from_both_roots():
    """Verify both ~/.claude/skills/ and ~/.agents/skills/ are scanned."""
    _reset()
    found_dirs = [r for r in _EXTERNAL_SKILL_ROOTS if os.path.isdir(r)]
    assert len(found_dirs) >= 2, (
        f"expected both skill roots to exist, got: {found_dirs}"
    )
    for r in _EXTERNAL_SKILL_ROOTS:
        assert os.path.isdir(r), f"skill root missing: {r}"
    assert len(SKILLS) > 50, (
        f"fewer than 50 skills registered ({len(SKILLS)}) - "
        "external skills may not be scanning properly"
    )


def test_gstack_sub_skills_are_registered():
    """Verify ~/.claude/skills/gstack/*/SKILL.md files are registered with category=gstack."""
    _reset()
    gstack_skills = [s for s in list_skills() if s["category"] == "gstack"]
    assert len(gstack_skills) >= 15, (
        f"expected 15+ gstack sub-skills, got {len(gstack_skills)}"
    )

    gstack_root = os.path.expanduser("~/.claude/skills/gstack")
    on_disk = set()
    if os.path.isdir(gstack_root):
        for name in os.listdir(gstack_root):
            p = os.path.join(gstack_root, name)
            if os.path.isdir(p) and os.path.isfile(os.path.join(p, "SKILL.md")):
                on_disk.add(name)

    registered_ids = {s["id"] for s in gstack_skills}
    expected_on_disk = {"ext." + name for name in on_disk}
    missing = expected_on_disk - registered_ids
    assert not missing, (
        f"gstack sub-dirs with SKILL.md but NOT registered: {sorted(missing)}"
    )


def test_gstack_skill_info_reads_frontmatter():
    """Verify gstack_skill_info returns actual SKILL.md metadata."""
    _reset()
    for skill_id in ["ext.investigate", "ext.qa", "ext.retro"]:
        result = gstack_skill_info({"skill_id": skill_id})
        assert not result.startswith("[error]"), f"{skill_id}: {result}"
        assert "category: gstack" in result, f"{skill_id}: missing category"
        assert "triggers:" in result, f"{skill_id}: missing triggers"
        assert "allowed_tools:" in result, f"{skill_id}: missing allowed_tools"
        assert "description:" in result, f"{skill_id}: missing description"
        assert "path:" in result, f"{skill_id}: missing path"


def test_multiple_categories_present():
    """Verify skills from different sources have distinct categories."""
    _reset()
    cats = {s["category"] for s in list_skills()}
    assert "external" in cats, "top-level skills should have category=external"
    assert "gstack" in cats, "gstack sub-skills should have category=gstack"
    # No other skill roots with sub-skill structure on this machine, so only 2
    assert len(cats) == 2, (
        f"expected exactly 2 categories (external, gstack), got: {cats}"
    )


def test_skill_info_returns_actual_triggers():
    """Verify gstack_skill_info returns real trigger keywords from SKILL.md frontmatter."""
    _reset()
    result = gstack_skill_info({"skill_id": "ext.investigate"})
    assert "debug" in result or "investigate" in result, (
        f"investigate skill should list debug/investigate triggers: {result}"
    )


def test_skill_info_returns_actual_allowed_tools():
    """Verify gstack_skill_info returns real allowed-tools from SKILL.md frontmatter."""
    _reset()
    result = gstack_skill_info({"skill_id": "ext.qa"})
    assert "Bash" in result or "browser" in result, (
        f"qa skill should list browser/Bash in allowed-tools: {result}"
    )


def test_non_gstack_external_skills_work():
    """Verify non-gstack external skills (e.g. remotion, convex) are registered."""
    _reset()
    ext = [s for s in list_skills() if s["category"] == "external"]
    assert len(ext) >= 5, (
        f"expected 5+ top-level external skills, got {len(ext)}: "
        f"{[s['name'] for s in ext]}"
    )


def test_skills_are_callable():
    """Verify each registered skill has a valid system_prompt.

    Length check only applies to natively-defined skills — externally-discovered
    ones (external_path set, from ~/.claude/skills or ~/.agents/skills) are
    personal/third-party content this repo doesn't own or control, and can be
    legitimately short (e.g. a one-line pointer to a slash command)."""
    _reset()
    for s in SKILLS:
        assert s.system_prompt, f"skill {s.id} has empty system_prompt"
        if s.external_path:
            continue
        assert len(s.system_prompt) > 50, (
            f"skill {s.id} has suspiciously short system_prompt "
            f"({len(s.system_prompt)} chars)"
        )


if __name__ == "__main__":
    test_skills_load_from_both_roots()
    test_gstack_sub_skills_are_registered()
    test_gstack_skill_info_reads_frontmatter()
    test_multiple_categories_present()
    test_skill_info_returns_actual_triggers()
    test_skill_info_returns_actual_allowed_tools()
    test_non_gstack_external_skills_work()
    test_skills_are_callable()
    print("OK — e2e: clixen reads skills from disk")
