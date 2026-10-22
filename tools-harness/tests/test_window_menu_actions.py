import subprocess

import pytest

from tools import window_menu_actions as wm


def _fake(monkeypatch, rc=0, out="", err=""):
    calls = []
    monkeypatch.setattr(wm, "_osascript", lambda script, *argv: calls.append(argv) or subprocess.CompletedProcess([], rc, out, err))
    return calls


def test_validation_never_runs_osascript(monkeypatch):
    monkeypatch.setattr(wm, "_osascript", lambda *a: (_ for _ in ()).throw(AssertionError("osascript called")))
    assert wm.window("bogus", "Finder").startswith("Unknown window action")
    assert wm.window("move", "Finder").startswith("move needs x and y")
    assert wm.window("resize", "Finder", width=0, height=5).startswith("resize needs positive")
    assert wm.window("focus", "Finder", index=0).startswith("index is 1-based")
    assert wm.menu("click", "Finder", "File").startswith("click needs a full path")
    assert wm.menu("list", "", "File").startswith("menu needs")


@pytest.mark.parametrize("item", ["Close Window", "Quit Finder", "Move to Trash", "Delete Immediately…", "Empty Trash…", "Log Out Jim…"])
def test_menu_refuses_destructive_items(monkeypatch, item):
    monkeypatch.setattr(wm, "_osascript", lambda *a: (_ for _ in ()).throw(AssertionError("osascript called")))
    assert wm.menu("click", "Finder", f"File > {item}").startswith("Refused")


def test_args_go_as_argv_in_order(monkeypatch):
    calls = _fake(monkeypatch, out="ok")
    wm.window("move", 'We"ird', 2, 10, 20)
    wm.menu("click", "Chrome", "File > Open Recent > A")
    assert calls == [('We"ird', "2", "10", "20"), ("Chrome", "File", "Open Recent", "A")]


def test_app_not_running_vs_missing_item(monkeypatch):
    _fake(monkeypatch, rc=1, err='Can’t get process "Nope". (-1728)')
    assert wm.menu("list", "Nope", "File").startswith("Error: app not running")
    _fake(monkeypatch, rc=1, err='Can’t get menu item "X" of menu 1 of menu bar item "File". (-1728)')
    assert not wm.menu("list", "Finder", "File > X").startswith("Error: app not running")
