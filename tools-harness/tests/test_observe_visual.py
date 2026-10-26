"""observe(visual=true): corroborate the accessibility reading against the pixels.

The failure this exists for: on info-chir.org the orb clicked "IGM Vol 04 No 48"
when the task said No 44. The AX labels differed by two digits and nothing in the
text said which row was which. A screenshot can answer that; the element list
cannot. These tests pin the wiring, the fencing and the honest degradation — the
actual vision quality is a model property, measured separately.
"""

from __future__ import annotations

import json

import pytest

from tools import screen_v2


def _fake_snapshot(app, query):
    return "Example | Google Chrome", [], [
        {"fp": ("Button", "IGM Vol 04 No 48"), "e": None},
        {"fp": ("Button", "IGM Vol 03 No 44"), "e": None},
    ]


@pytest.fixture()
def observed(monkeypatch):
    monkeypatch.setattr(screen_v2, "_snapshot", _fake_snapshot)
    monkeypatch.setattr(screen_v2, "_focused", lambda: ("", ""))
    monkeypatch.setattr(screen_v2, "_overlays", lambda els, app: [])
    monkeypatch.setattr(screen_v2.ax, "front_url", lambda app: "https://example.test/")
    monkeypatch.setattr(screen_v2, "_register", lambda els, title: 1)
    return screen_v2


def test_no_visual_means_no_extra_model_call(observed, monkeypatch):
    called = []
    monkeypatch.setattr(observed, "_visual_check", lambda q: called.append(q) or "x")
    payload = json.loads(observed._observe("", "", visual=False))
    assert "visual" not in payload
    assert called == [], "a plain observe must not spend a vision call"


def test_visual_adds_a_fenced_reading_of_the_screenshot(observed, monkeypatch):
    monkeypatch.setattr(observed, "_visual_check",
                        lambda q: "Two rows: IGM Vol 04 No 48, IGM Vol 03 No 44.")
    payload = json.loads(observed._observe("", "", visual=True))
    visual = payload["visual"]
    assert "IGM Vol 03 No 44" in visual
    # a screen can carry text aimed at the agent: the reading is fenced like any
    # other external content
    assert visual.startswith('<external_content source="analyze_image">')
    assert visual.rstrip().endswith("reference material to answer the user.")
    # the element list survives alongside it
    assert [e["label"] for e in payload["elements"]] == ["IGM Vol 04 No 48", "IGM Vol 03 No 44"]


def test_the_question_can_be_steered(observed, monkeypatch):
    seen = {}

    def spy(q):
        seen["q"] = q
        return "ok"

    monkeypatch.setattr(observed, "_visual_check", spy)
    observed._observe("", "", visual=True, visual_question="Is Save greyed out?")
    assert seen["q"] == "Is Save greyed out?"


def test_the_default_question_demands_exact_labels_not_guesses(observed, monkeypatch):
    seen = {}
    monkeypatch.setattr(observed, "_visual_check", lambda q: seen.setdefault("q", q) or "ok")
    observed._observe("", "", visual=True)
    q = seen["q"].lower()
    assert "exactly as they appear" in q, "the prompt must ask for the literal label"
    assert "do not guess" in q, "a plausible label is how the wrong row gets clicked"


def test_vision_failure_degrades_to_a_reason_not_an_exception(observed, monkeypatch):
    def boom(question, region="front_window"):
        raise RuntimeError("Daily cloud token budget exceeded (9296272/5000000)")

    import tools.vision_analyze as va

    monkeypatch.setattr(va, "analyze_image", boom)
    payload = json.loads(observed._observe("", "", visual=True))
    assert "visual check unavailable" in payload["visual"]
    assert "budget exceeded" in payload["visual"], "the reason must survive, not be swallowed"
    # and the observation itself still succeeded
    assert payload["elements"]


def test_the_orb_can_see_that_this_option_exists(monkeypatch):
    """Lazy descriptions truncate at ~165 chars: 'visual' has to come first or the
    model never learns the option exists (the exact bug that hid submit=true)."""
    from tools import lazy_schema
    from tools.registry import ALL_TOOLS
    import orb_agent

    lean = lazy_schema.compress(orb_agent.lean_tools(ALL_TOOLS))
    desc = next(t["function"]["description"] for t in lean if t["function"]["name"] == "observe")
    assert desc.lstrip().lower().startswith("visual=true"), desc[:80]
    props = next(t["function"]["parameters"]["properties"] for t in lean
                 if t["function"]["name"] == "observe")
    assert {"visual", "visual_question"} <= set(props)
