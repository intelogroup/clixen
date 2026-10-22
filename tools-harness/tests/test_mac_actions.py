import json
import subprocess

import pytest

from tools import mac_actions as ma


def _pb(monkeypatch, out="", rc=0):
    calls = []
    monkeypatch.setattr(ma, "_pb", lambda args: calls.append(args) or subprocess.CompletedProcess(args, rc, out, ""))
    return calls


@pytest.mark.parametrize("name", ["Finder", "finder.app", "Clixen Orb", "Dock", "loginwindow"])
def test_quit_refuses_system_and_orb(monkeypatch, name):
    calls = _pb(monkeypatch)
    assert ma.app("quit", name).startswith("Refused")
    assert calls == []


def test_app_commands_and_list(monkeypatch):
    calls = _pb(monkeypatch, json.dumps({"data": {"apps": [{"name": "Calculator", "is_active": True}, {"name": "Notes"}]}}))
    assert ma.app("list") == "Calculator (frontmost)\nNotes"
    assert ma.app("quit", "Calculator") == "Quit Calculator."
    assert calls[-1] == ["app", "quit", "--app", "Calculator"]
    assert ma.app("switch").startswith("app switch needs a name")


def test_dialog_guards_and_empty(monkeypatch):
    calls = _pb(monkeypatch, json.dumps({"data": {"title": "", "buttons": ["", "Siri"], "textElements": []}}))
    assert ma.dialog("input", text="hunter2", field="Password").startswith("Refused")
    assert ma.dialog("click").startswith("dialog click needs")
    assert ma.dialog("list") == "No dialog on screen."
    assert len(calls) == 1


def test_dock_space_list_and_validation(monkeypatch):
    _pb(monkeypatch, json.dumps({"data": {"dockItems": [{"title": "Finder", "running": True}, {"title": "Mail"}]}}))
    assert ma.dock("list") == "Finder (running)\nMail"
    assert ma.dock("launch").startswith("dock launch needs")
    _pb(monkeypatch, json.dumps({"data": {"spaces": [{"id": 1, "is_active": True}]}}))
    assert ma.space("list") == "Space 1 (active)"
    assert ma.space("switch", 0).startswith("space switch needs")


def test_system_validation(monkeypatch):
    monkeypatch.setattr(ma, "_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    assert ma.system("set_volume", level=150).startswith("set_volume needs")
    assert ma.system("notify").startswith("notify needs")
    assert ma.system("bogus").startswith("Unknown system action")
