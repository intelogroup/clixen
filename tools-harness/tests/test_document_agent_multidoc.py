from types import SimpleNamespace


def test_two_document_question_uses_both_sources_and_one_model_call(tmp_path, monkeypatch):
    from agents import document_agent

    relevant = tmp_path / "contract.txt"
    distractor = tmp_path / "notes.txt"
    relevant.write_text("Termination notice: 30 days.\n")
    distractor.write_text("Office snacks: coffee and tea.\n")
    calls = []

    def fake_chat(model, messages, tools, temperature=None):
        calls.append({"model": model, "messages": messages, "tools": tools})
        return SimpleNamespace(message=SimpleNamespace(
            content="The contract requires 30 days' notice [E1].",
            tool_calls=None,
        ))

    monkeypatch.setattr(document_agent, "_chat", fake_chat)
    answer = document_agent.run_document_agent(
        f"What is the termination notice? Compare {relevant} with {distractor}.",
        model="gemma4:12b",
    )

    assert len(calls) == 1
    assert calls[0]["tools"] == []
    prompt = calls[0]["messages"][0]["content"]
    assert "Termination notice: 30 days." in prompt
    assert "Office snacks: coffee and tea." in prompt
    assert f"[E1] {relevant} — document" in answer


def test_document_agent_does_not_call_gemma_when_only_preparing_evidence(monkeypatch):
    from agents import document_agent

    called = []
    monkeypatch.setattr(document_agent, "_chat", lambda **kwargs: called.append(kwargs))
    # The public operation always synthesizes; this guard documents that the
    # evidence preparation itself has no hidden model call.
    document_agent._prepare_evidence("summarize missing.pdf", None)

    assert called == []


def test_mixed_request_sends_only_public_query_to_cloud(tmp_path, monkeypatch):
    from agents import document_agent

    source = tmp_path / "private report.txt"
    source.write_text("Private finding: local baseline is 10.")
    calls = []

    def fake_execute(name, args):
        calls.append((name, args))
        if name == "read_document":
            return source.read_text()
        if name == "ask_web_search":
            return "Public current benchmark is 12."
        raise AssertionError(name)

    monkeypatch.setattr(document_agent, "execute_tool", fake_execute)
    monkeypatch.setattr(
        document_agent,
        "_chat",
        lambda **kwargs: SimpleNamespace(
            message=SimpleNamespace(content="Local is 10 and public is 12 [E1] [E2].", tool_calls=None)
        ),
    )

    result = document_agent.run_document_agent(
        f"Compare {source} against the current public benchmark", model="gemma4:12b"
    )

    web_query = next(args["query"] for name, args in calls if name == "ask_web_search")
    assert str(source) not in web_query
    assert "current public benchmark" in web_query
    assert "cloud web search" in result
