"""Every orb tool must be visible.

The orb is a visual agent: a headless tool that runs silently is a violation of what the
product is, not an optimisation. Measured before this suite existed: 4 of 51 orb tools
animated, and a six-call research task (page_probe -> scrapling_api_capture ->
workspace_download) showed the bunny sitting still for twenty seconds.

Each test below corresponds to a defect that shipped at least once.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1]
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from tools import orb_cursor, visual_contract  # noqa: E402


@pytest.fixture()
def feed(tmp_path, monkeypatch):
    """Point the cursor feed at a temp file so a real orb is never disturbed."""
    f = tmp_path / "orb_cursor.json"
    monkeypatch.setattr(orb_cursor, "_FILE", f)
    monkeypatch.setattr(orb_cursor, "_LAST_ANCHOR", None)
    return f


def _payload(f: Path) -> dict:
    return json.loads(f.read_text())


def test_every_orb_tool_has_a_gesture(feed):
    """The whole point: silence becomes a build error, not a habit."""
    import orb_agent

    missing = sorted(set(orb_agent.TOOL_NAMES) - set(visual_contract.GESTURES))
    assert not missing, f"these orb tools would run invisibly: {missing}"


def test_anchors_are_known_kinds(feed):
    allowed = {"element", "browser", "front", "orb"}
    bad = {t: a for t, (_, a) in visual_contract.GESTURES.items() if a not in allowed}
    assert not bad, f"unknown anchor kinds: {bad}"


def test_a_gesture_carries_its_anchor(feed):
    """point() wrote {x,y} and then scrolling() replaced the whole file with {"scroll":…},
    so the bunny never learned where to go. A gesture must never erase the anchor."""
    orb_cursor.point(700.0, 400.0, click=False)
    orb_cursor.reading("observe")
    p = _payload(feed)
    assert (p["x"], p["y"]) == (700.0, 400.0)
    assert "read" in p


def test_gesture_keys_do_not_persist(feed):
    """A stale click:true must never re-fire on the next event."""
    orb_cursor.point(10.0, 20.0, click=True)
    orb_cursor.thinking("remember")
    p = _payload(feed)
    assert "click" not in p, p


def test_announce_emits_the_right_gesture(feed):
    visual_contract.announce("scrapling_api_capture", {})
    assert "read" in _payload(feed)
    visual_contract.announce("workspace_download", {})
    assert "carry" in _payload(feed)
    visual_contract.announce("system_status", {})
    assert "think" in _payload(feed)


def test_think_does_not_teleport_the_bunny(feed):
    """'What time is it' happens at the orb. Flying to a window would be a lie about where
    the work happened — so a think keeps the anchor it already had."""
    orb_cursor.point(500.0, 300.0, click=False)
    visual_contract.announce("get_current_time", {})
    p = _payload(feed)
    assert (p["x"], p["y"]) == (500.0, 300.0)
    assert "think" in p


def test_a_failure_never_plays_a_success_pose(feed):
    visual_contract.settle("workspace_download", ok=False)
    assert "fail" in _payload(feed)
    before = feed.read_text()
    visual_contract.settle("workspace_download", ok=True)
    assert feed.read_text() == before, "a success must not re-animate"


def test_announce_never_raises(feed, monkeypatch):
    """Animation must never be able to fail the tool the user asked for."""
    monkeypatch.setattr(orb_cursor, "_write", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    visual_contract.announce("observe", {})  # must not raise
    visual_contract.settle("observe", False)


def test_unknown_tool_is_silent_but_safe(feed):
    assert visual_contract.announce("no_such_tool", {}) is None
