import time

from tools import screen_watch as sw


def test_blocked():
    assert sw.is_blocked("1Password 7", "Vault")
    assert sw.is_blocked("Safari", "Private Browsing — Google")
    assert not sw.is_blocked("Ghostty", "build")


def test_frame_changed():
    a = [10] * 1024
    assert sw.frame_changed(None, a)
    assert not sw.frame_changed(a, [11] * 1024)  # noise under threshold
    assert sw.frame_changed(a, [200] * 1024)


def test_history_collapse_and_prune():
    now = time.time()
    h = sw.add_history([], {"ts": now - 700, "app": "A", "title": "old", "text": ""}, now)
    h = sw.add_history(h, {"ts": now - 5, "app": "B", "title": "x", "text": "1"}, now)
    h = sw.add_history(h, {"ts": now, "app": "B", "title": "x", "text": "2"}, now)
    assert [e["text"] for e in h] == ["2"]  # old pruned, repeat collapsed


def test_toggle_and_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(sw, "_FLAG_FILE", tmp_path / "flag")
    monkeypatch.setattr(sw, "_STATE_FILE", tmp_path / "state.json")
    assert "off" in sw.get_screen_context().lower()
    sw.set_screen_watch(True)
    sw._write_state({"app": "Ghostty", "title": "t", "text": "hello", "updated_at": time.time(), "history": []})
    assert "hello" in sw.get_screen_context()
    sw.set_screen_watch(False)
    assert not (tmp_path / "state.json").exists()
