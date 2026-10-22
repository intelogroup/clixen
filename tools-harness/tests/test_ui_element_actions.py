import json
import subprocess

from tools import ui_element_actions as ua

SEE = {
    "success": True,
    "data": {
        "application_name": "Finder", "window_title": "Applications", "interactable_count": 5, "snapshot_id": "S1",
        "ui_elements": [
            {"id": "e1", "role": "button", "label": "back", "is_actionable": True},
            {"id": "e2", "role": "button", "label": "close button", "is_actionable": True},
            {"id": "e3", "role": "other", "label": "Downloads", "is_actionable": True},
            {"id": "e4", "role": "image", "label": "image", "is_actionable": True},
            {"id": "e5", "role": "button", "label": "", "is_actionable": True},
            {"id": "e6", "role": "button", "label": "Hidden", "is_actionable": False},
        ],
    },
}


def _setup(monkeypatch, calls):
    ua._last_seen.clear()
    ua._last_center.clear()
    monkeypatch.setattr(ua, "_peekaboo", lambda: "/x/peekaboo")
    monkeypatch.setattr(ua, "_centers", lambda sid: {"e1": (10, 20), "e2": (30, 40), "e3": (421, 302)})

    def run(args, timeout=25):
        calls.append(args)
        out = json.dumps(SEE) if args[1] == "see" else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(ua, "_run", run)


def test_see_lists_only_labelled_actionable(monkeypatch):
    _setup(monkeypatch, [])
    out = ua.ui_see("Finder")
    assert out.splitlines()[1:] == ["e1 button back", "e2 button close button", "e3 other Downloads"]


def test_click_requires_latest_see(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    assert ua.ui_click("e1", "Finder").startswith("Refused: e1 was not returned")
    assert calls == []


def test_click_refuses_close_controls(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    ua.ui_see("Finder")
    assert ua.ui_click("e2", "Finder").startswith("Refused")
    assert len(calls) == 1  # only the see call


def test_click_uses_frame_center_coords(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    ua.ui_see("Finder")
    assert ua.ui_click("e3", "Finder", double=True) == "Clicked e3 (Downloads)."
    assert calls[-1] == ["/x/peekaboo", "click", "--coords", "421,302", "--app", "Finder", "--double"]


def test_centers_reads_snapshot_frames(monkeypatch, tmp_path):
    d = tmp_path / ".peekaboo" / "snapshots" / "S1"
    d.mkdir(parents=True)
    (d / "snapshot.json").write_text(json.dumps({"children": [
        {"id": "elem_29", "frame": [[347, 293], [148, 18]]},
        {"id": "menuitem_1", "frame": [[0, 956], [0, 0]]},
    ]}))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ua._centers("S1") == {"elem_29": (421, 302)}
    assert ua._centers("missing") == {}


def test_drag_uses_both_centers_and_requires_see(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    assert ua.ui_drag("e1", "e3", "Finder").startswith("Refused")
    ua.ui_see("Finder")
    assert ua.ui_drag("e1", "e3", "Finder") == "Dragged e1 (back) onto e3 (Downloads)."
    assert calls[-1] == ["/x/peekaboo", "drag", "--from-coords", "10,20", "--to-coords", "421,302", "--app", "Finder"]
