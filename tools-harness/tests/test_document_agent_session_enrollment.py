from types import SimpleNamespace


def test_document_session_enrolls_touched_file_without_changing_answer(tmp_path, monkeypatch):
    from agents import document_agent

    source = tmp_path / "report.txt"
    source.write_text("Revenue: 42")
    enrolled = []
    monkeypatch.setattr("tools.document_session._DB_PATH", tmp_path / "session.db")

    monkeypatch.setattr(document_agent, "_chat", lambda **kwargs: SimpleNamespace(
        message=SimpleNamespace(content="Revenue is 42 [E1].", tool_calls=None),
    ))
    monkeypatch.setattr(
        "tools.semantic_files.index_directory",
        lambda path, glob="*", refresh=False: enrolled.append((path, glob, refresh)),
    )

    result = document_agent.run_document_agent(
        f"Summarize {source}",
        model="gemma4:12b",
        chat_id="chat-1",
        project_root=str(tmp_path),
    )

    assert "Revenue is 42" in result
    assert enrolled == [(str(tmp_path), "report.txt", False)]
