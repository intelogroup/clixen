"""Per-skill trigger-coverage: every skill with trigger_keywords/trigger_regex
must match SOME query built from its own triggers (catches false negatives —
skills that silently never fire per skills_hub.py's match_skill None path).

Does not assert which skill wins on overlap/ties — only that match_skill
does not return None for a skill's own trigger phrase.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skills_hub import SKILLS, match_skill


def _query_for(skill):
    if skill.trigger_keywords:
        return " ".join(skill.trigger_keywords[:3])
    return None


def test_every_skill_trigger_matches_something():
    failures = []
    for s in SKILLS:
        query = _query_for(s)
        if not query:
            continue
        if match_skill(query) is None:
            failures.append(f"{s.id}: query={query!r} matched nothing")
    assert not failures, "skills with dead triggers:\n" + "\n".join(failures)


if __name__ == "__main__":
    test_every_skill_trigger_matches_something()
    print("OK")
