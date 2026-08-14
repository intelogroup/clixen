from tools.document_session import recall, remember


def test_session_cache_reuses_current_workspace_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.document_session._DB_PATH", tmp_path / "session.db")
    source = tmp_path / "report.txt"
    source.write_text("Revenue 42")
    chunk = {"source": str(source), "locator": "Summary", "text": "Revenue 42"}

    assert remember("chat-1", str(tmp_path), [chunk]) == 1
    cached = recall("chat-1", str(tmp_path))

    assert cached == [{"source": str(source.resolve()), "locator": "Summary", "text": "Revenue 42"}]


def test_session_cache_drops_changed_files(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.document_session._DB_PATH", tmp_path / "session.db")
    source = tmp_path / "report.txt"
    source.write_text("old")
    remember("chat-1", str(tmp_path), [{"source": str(source), "locator": "document", "text": "old"}])
    source.write_text("new")

    assert recall("chat-1", str(tmp_path)) == []


def test_session_cache_rejects_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.document_session._DB_PATH", tmp_path / "session.db")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")

    assert remember("chat-1", str(tmp_path), [{"source": str(outside), "text": "secret"}]) == 0
