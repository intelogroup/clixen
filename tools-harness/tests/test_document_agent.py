from types import SimpleNamespace

import pytest


def test_document_agent_reads_explicit_documents_and_synthesizes_once(tmp_path, monkeypatch):
    from agents import document_agent

    source = tmp_path / "report.txt"
    source.write_text("Revenue: $12M\nRisk: supplier concentration\n")
    calls = []

    def fake_chat(model, messages, tools, temperature=0.0):
        calls.append((model, messages, tools, temperature))
        return SimpleNamespace(message=SimpleNamespace(
            content="Revenue is $12M. The report identifies supplier concentration as a risk.",
            tool_calls=None,
        ))

    monkeypatch.setattr(document_agent, "_chat", fake_chat)
    result = document_agent.run_document_agent(
        f"Summarize {source} and cite the evidence.",
        model="gemma4:12b",
    )

    assert result.startswith("Revenue is $12M")
    assert len(calls) == 1
    assert calls[0][2] == []
    assert "Revenue: $12M" in calls[0][1][-1]["content"]
    assert str(source) in calls[0][1][-1]["content"]
    assert "[E1 | SOURCE:" in calls[0][1][-1]["content"]


def test_document_agent_honors_cancellation_before_synthesis(tmp_path, monkeypatch):
    from agents import document_agent
    from clients.cancellation import QueryAbortedException

    source = tmp_path / "cancel.txt"
    source.write_text("should never reach the model")
    monkeypatch.setattr(
        "clients.cancellation.check_aborted",
        lambda: (_ for _ in ()).throw(QueryAbortedException("cancelled")),
    )
    with pytest.raises(QueryAbortedException):
        document_agent.run_document_agent(f"Summarize {source}", model="gemma4:12b")


def test_document_agent_uses_one_correction_call_for_unsupported_citation(tmp_path, monkeypatch):
    from agents import document_agent

    source = tmp_path / "facts.txt"
    source.write_text("Term: 30 days\n")
    calls = []

    def fake_chat(model, messages, tools, temperature=0.0):
        calls.append(messages[0]["content"])
        content = "The term is 90 days [E1]." if len(calls) == 1 else "The term is 30 days [E1]."
        return SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))

    monkeypatch.setattr(document_agent, "_chat", fake_chat)
    result = document_agent.run_document_agent(f"Summarize {source}", model="gemma4:12b")

    assert len(calls) == 2
    assert "The term is 30 days" in result
    assert "GROUNDING WARNINGS" not in result


def test_document_agent_corrects_formula_backed_numeric_refusal(tmp_path, monkeypatch):
    from agents import document_agent

    source = tmp_path / "report.md"
    source.write_text("| Q2 | 100,000 |\nQ3 grew by exactly 20% over Q2 revenue.")
    calls = []

    def fake_chat(model, messages, tools, temperature=0.0):
        calls.append(messages[0]["content"])
        answer = "The Q3 revenue is not established."
        return SimpleNamespace(message=SimpleNamespace(content=answer, tool_calls=None))

    monkeypatch.setattr(document_agent, "_chat", fake_chat)
    result = document_agent.run_document_agent(f"What is Q3 revenue from {source}?", model="gemma4:12b")

    assert len(calls) == 2
    assert "120000" in result


def test_document_agent_has_small_document_manifest():
    from agents.document_agent import DOCUMENT_OPERATIONS

    assert DOCUMENT_OPERATIONS == {
        "document_retrieve",
        "read_document",
        "fulltext_search",
        "semantic_file_search",
        "write_file",
        "edit_file",
        "quarantine_document",
        "forget_document_index",
        "delete_document",
    }


def test_document_agent_uses_one_academic_connector_for_research_query(monkeypatch):
    from agents import document_agent

    calls = []

    def fake_execute(name, args):
        calls.append((name, args))
        if name == "search_pubmed":
            return "Title: Local clinical study\nAbstract: A bounded result."
        raise AssertionError(f"unexpected tool: {name}")

    monkeypatch.setattr(document_agent, "execute_tool", fake_execute)
    monkeypatch.setattr(
        document_agent,
        "_chat",
        lambda **kwargs: SimpleNamespace(message=SimpleNamespace(content="Result [E1]", tool_calls=None)),
    )

    result = document_agent.run_document_agent("Find PubMed research about clinical trials")

    assert calls == [("search_pubmed", {"query": "Find PubMed research about clinical trials", "max_results": 6})]
    assert "[E1] search_pubmed" in result


def test_document_agent_routes_reference_query_to_wikipedia(monkeypatch):
    from agents import document_agent

    calls = []

    def fake_execute(name, args):
        calls.append((name, args))
        return "Title: Local encyclopedia entry\nSummary: A bounded reference result."

    monkeypatch.setattr(document_agent, "execute_tool", fake_execute)
    monkeypatch.setattr(
        document_agent,
        "_chat",
        lambda **kwargs: SimpleNamespace(message=SimpleNamespace(content="Answer [E1]", tool_calls=None)),
    )

    result = document_agent.run_document_agent("Use Wikipedia to explain the topic")

    assert calls == [("search_wikipedia", {"query": "Use Wikipedia to explain the topic", "limit": 6})]
    assert "[E1] search_wikipedia" in result


def test_document_agent_uses_cloud_web_search_for_open_ended_current_query(monkeypatch):
    from agents import document_agent

    calls = []

    def fake_execute(name, args):
        calls.append((name, args))
        assert name == "ask_web_search"
        return "Mount Everest is 8,848.86 meters tall."

    monkeypatch.setattr(document_agent, "execute_tool", fake_execute)
    monkeypatch.setattr(
        document_agent,
        "_chat",
        lambda **kwargs: SimpleNamespace(
            message=SimpleNamespace(content="Mount Everest is 8,848.86 meters [E1].", tool_calls=None)
        ),
    )

    result = document_agent.run_document_agent("Look up the current height of Mount Everest")

    assert calls == [("ask_web_search", {"query": "Look up the current height of Mount Everest"})]
    assert "cloud web search" in result


def test_document_agent_keeps_explicit_local_document_off_cloud_search(tmp_path, monkeypatch):
    from agents import document_agent

    source = tmp_path / "private.txt"
    source.write_text("Internal revenue is $12M.")
    calls = []

    def fake_execute(name, args):
        calls.append(name)
        if name == "read_document":
            return source.read_text()
        raise AssertionError(f"unexpected external call: {name}")

    monkeypatch.setattr(document_agent, "execute_tool", fake_execute)
    monkeypatch.setattr(
        document_agent,
        "_chat",
        lambda **kwargs: SimpleNamespace(
            message=SimpleNamespace(content="Revenue is $12M [E1].", tool_calls=None)
        ),
    )

    document_agent.run_document_agent(f"Summarize {source}")

    assert calls == ["read_document"]


def test_document_agent_announces_private_scratch_workspace(tmp_path, monkeypatch):
    from agents import document_agent

    source = tmp_path / "notes.txt"
    source.write_text("Keep this fact.")
    captured = {}
    monkeypatch.setattr(
        document_agent,
        "execute_tool",
        lambda name, args: source.read_text() if name == "read_document" else "",
    )
    monkeypatch.setattr(
        document_agent,
        "_chat",
        lambda **kwargs: (captured.update(kwargs) or SimpleNamespace(
            message=SimpleNamespace(content="Keep this fact [E1].", tool_calls=None)
        )),
    )

    document_agent.run_document_agent(
        f"Summarize {source}", chat_id="scratch-chat", project_root=str(tmp_path)
    )

    prompt = captured["messages"][0]["content"]
    assert ".clixen-scratch" in prompt
    assert "Do not treat it as an export destination" in prompt


def test_explicit_missing_document_wins_over_academic_connector(monkeypatch):
    from agents import document_agent

    calls = []
    prompts = []
    monkeypatch.setattr(document_agent, "execute_tool", lambda name, args: calls.append(name))
    monkeypatch.setattr(
        document_agent,
        "_chat", lambda **kwargs: (
            prompts.append(kwargs["messages"][0]["content"])
            or SimpleNamespace(message=SimpleNamespace(content="Missing", tool_calls=None))
        ),
    )

    result = document_agent.run_document_agent("Summarize missing-paper.pdf from PubMed")

    assert "SOURCE NOT FOUND" in prompts[0]
    assert calls == []


def test_empty_academic_connector_result_is_reported_without_fabrication(monkeypatch):
    from agents import document_agent

    prompts = []
    monkeypatch.setattr(document_agent, "execute_tool", lambda name, args: "[pubmed] no results")
    monkeypatch.setattr(
        document_agent,
        "_chat", lambda **kwargs: (
            prompts.append(kwargs["messages"][0]["content"])
            or SimpleNamespace(message=SimpleNamespace(content="No reliable result", tool_calls=None))
        ),
    )

    result = document_agent.run_document_agent("Find PubMed research on an unknown topic")

    assert "no results" in prompts[0]
    assert "CITATION WARNING: no evidence IDs cited" in result


def test_research_source_priority_is_deterministic():
    from agents.document_agent import _research_source

    assert _research_source("Wikipedia article about clinical trials") == "search_wikipedia"
    assert _research_source("PubMed clinical trial evidence") == "search_pubmed"
    assert _research_source("SEC 10-K company filing") == "search_sec_edgar"
    assert _research_source("recent GDELT news") == "search_gdelt_news"


def test_document_agent_does_not_replace_missing_explicit_path_with_unrelated_search(monkeypatch):
    from agents import document_agent

    monkeypatch.setattr(
        document_agent,
        "_chat",
        lambda **kwargs: SimpleNamespace(message=SimpleNamespace(
            content=kwargs["messages"][0]["content"], tool_calls=None,
        )),
    )
    result = document_agent.run_document_agent(
        "Summarize missing-report.pdf", model="gemma4:12b"
    )

    assert "SOURCE NOT FOUND" in result
    assert "SEARCH EVIDENCE" not in result


def test_document_task_uses_dedicated_path_in_streaming_entrypoint(monkeypatch):
    from agents import local_agent_graph

    called = {}

    def fake_run(query, model, chat_id=None, project_root=None):
        called.update(query=query, model=model, chat_id=chat_id, project_root=project_root)
        return "document result"

    monkeypatch.setattr(local_agent_graph, "run_document_agent", fake_run)
    events = list(local_agent_graph.run_local_agent_streaming(
        "summarize report.txt", model="gemma4:12b", task="document"
    ))

    assert events == [("final", "document result")]
    assert called["model"] == "gemma4:12b"


def test_pdf_export_requires_explicit_path_and_approval(tmp_path, monkeypatch):
    from agents import document_agent

    calls = []
    monkeypatch.setattr(document_agent, "_chat", lambda **kwargs: SimpleNamespace(
        message=SimpleNamespace(content="# Draft", tool_calls=None),
    ))
    monkeypatch.setattr(
        document_agent,
        "request_confirmation",
        lambda name, args, command: calls.append((name, args, command)) or "tok-1",
    )

    result = document_agent.run_document_agent(
        f"Summarize {tmp_path / 'report.txt'} and export as PDF to {tmp_path / 'out.pdf'}",
        model="gemma4:12b",
    )

    assert "awaiting approval" in result
    assert calls[0][0] == "create_pdf"
    assert calls[0][1]["output_path"] == str(tmp_path / "out.pdf")


def test_document_agent_discovers_supported_files_only_inside_project_root(tmp_path, monkeypatch):
    from agents import document_agent

    (tmp_path / "report.txt").write_text("Local finding: 42.")
    (tmp_path / "photo.png").write_bytes(b"not an OCR fixture")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("Must not enter workspace evidence.")
    captured = {}
    monkeypatch.setattr(document_agent, "_chat", lambda **kwargs: (
        captured.update(kwargs) or SimpleNamespace(message=SimpleNamespace(
            content="The finding is 42 [E1].", tool_calls=None,
        ))
    ))

    document_agent.run_document_agent(
        "Find the local finding", model="gemma4:12b", project_root=str(tmp_path)
    )

    prompt = captured["messages"][0]["content"]
    assert "Local finding: 42." in prompt
    assert "Must not enter workspace evidence." not in prompt
