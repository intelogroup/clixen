"""Browse Visually skill: routing eval (5 TP / 5 TN) + regression guard.

2026-10-26: the PH10 solo run took ~30 observe/act rounds with no skill
matched — browse_web won at 6.5 with no visual discipline, and
"scroll down and click" routed to send_email_reply. This pins the fix:
multi-step UI navigation must match browse_visually, and existing winners
must not move.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skills_hub import SKILLS, get_skill, match_skill
from tools.registry import EXECUTORS

TP = [
    "Go to Product Hunt (https://www.producthunt.com), scroll down to find todays product number 10, open its page, and type exactly this in the comment box: Thats an amazing product. HARD RULES: do NOT send, submit, or post the comment.",
    "scroll down to the fifth item and click it, don't buy anything",
    "open amazon, find todays deal number 3 and add a comment, don't submit",
    "go to example.com, fill in the signup form but don't submit",
    "click the login button on github.com and tell me what happens, don't log in",
]

# (query, expected_winner_id): must NOT route to browse_visually.
TN = [
    ("qa test this website", "ext.qa"),
    ("search web for latest ai news", "web_search"),
    ("list my tasks", "list_tasks"),
    ("debug this login failure", "ext.investigate"),
    ("search arxiv for papers", "arxiv_search"),
]


def test_skill_registered_with_valid_tools():
    s = get_skill("browse_visually")
    assert s is not None, "browse_visually not registered"
    assert s.category == "Browser", s.category
    assert s.max_rounds <= 12, s.max_rounds
    for tool in s.tools:
        assert tool in EXECUTORS, f"browse_visually requires {tool!r} not in EXECUTORS"


def test_true_positives_match_browse_visually():
    failures = []
    for q in TP:
        w = match_skill(q)
        if w is None or w.id != "browse_visually":
            failures.append(f"{q[:60]!r} -> {w.id if w else None}")
    assert not failures, "TP misses:\n" + "\n".join(failures)


def test_true_negatives_keep_winners():
    failures = []
    for q, expected in TN:
        w = match_skill(q)
        if w is None or w.id != expected:
            failures.append(f"{q[:60]!r} -> {w.id if w else None} (want {expected})")
    assert not failures, "TN regressions:\n" + "\n".join(failures)


def test_login_wall_query_matches_with_margin():
    # The exact failure shape from the solo run must match decisively,
    # not squeak past on token noise.
    from skills_hub import _score_skill
    q = ("open its page, and type exactly this in the comment box. "
         "HARD RULES: do NOT send, submit, or post the comment.")
    scored = sorted(((_score_skill(s, q.lower()), s.id) for s in SKILLS), reverse=True)
    assert scored[0][1] == "browse_visually", scored[:3]
    assert scored[0][0] - scored[1][0] >= 3.0, scored[:3]
