from types import SimpleNamespace


def test_document_followup_uses_prior_user_document_context(tmp_path, monkeypatch):
    from agents import document_agent

    source = tmp_path / "report.txt"
    source.write_text("Term: 30 days")
    captured = {}
    monkeypatch.setattr(
        "store.conversation.get",
        lambda _chat_id: [{"role": "user", "content": f"Read {source}"}],
    )
    monkeypatch.setattr(document_agent, "_chat", lambda **kwargs: (
        captured.update(kwargs) or SimpleNamespace(
            message=SimpleNamespace(content="The term is 30 days [E1].", tool_calls=None),
        )
    ))

    document_agent.run_document_agent(
        "What is the term?", model="gemma4:12b", chat_id="chat-followup", project_root=str(tmp_path)
    )

    prompt = captured["messages"][0]["content"]
    assert str(source) in prompt
    assert "CONVERSATION CONTEXT" in prompt
