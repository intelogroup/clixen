import subprocess

from tools import finder_actions as fa


def test_missing_path_never_runs_osascript(monkeypatch):
    monkeypatch.setattr(fa, "_osascript", lambda *a: (_ for _ in ()).throw(AssertionError("osascript called")))
    assert fa.finder("open_folder", "/no/such/dir12345").startswith("Path not found")
    assert fa.finder("open_folder", "").startswith("finder open_folder needs a path")
    assert fa.finder("bogus").startswith("Unknown finder action")


def test_open_folder_rejects_file(monkeypatch, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    monkeypatch.setattr(fa, "_osascript", lambda *a: (_ for _ in ()).throw(AssertionError("osascript called")))
    assert fa.finder("open_folder", str(f)).startswith("Not a folder")


def test_open_and_reveal_use_open_not_osascript(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(fa, "_osascript", lambda *a: (_ for _ in ()).throw(AssertionError("osascript called")))
    monkeypatch.setattr(fa.subprocess, "run", lambda args, **k: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    d = tmp_path / 'we"ird'
    d.mkdir()
    assert fa.finder("open_folder", str(d)).startswith("Opened")
    assert fa.finder("reveal", str(d)).startswith("Revealed")
    assert calls == [["/usr/bin/open", str(d.resolve())], ["/usr/bin/open", "-R", str(d.resolve())]]


def test_empty_selection_and_error(monkeypatch):
    monkeypatch.setattr(fa, "_osascript", lambda *a: subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    assert fa.finder("selection") == "Nothing selected in Finder."
    assert fa.finder("front_path") == "No Finder window open."
    monkeypatch.setattr(fa, "_osascript", lambda *a: subprocess.CompletedProcess([], 1, stdout="", stderr="boom"))
    assert fa.finder("selection") == "Error: boom"
