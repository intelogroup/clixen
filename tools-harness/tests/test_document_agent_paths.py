def test_document_agent_finds_absolute_paths_with_spaces(tmp_path):
    from agents.document_agent import _candidate_paths

    first = tmp_path / "Scientific American issue.pdf"
    second = tmp_path / "Pathoma Slides.zip"
    first.write_bytes(b"pdf")
    second.write_bytes(b"zip")

    candidates, missing = _candidate_paths(
        f"Summarize '{first}' and {second}", None
    )

    assert candidates == [first.resolve(), second.resolve()]
    assert missing == []


def test_document_agent_finds_path_with_spaces_before_followup_words(tmp_path):
    from agents.document_agent import _candidate_paths

    source = tmp_path / "Quarterly report with spaces.pdf"
    source.write_bytes(b"pdf")

    candidates, missing = _candidate_paths(
        f"Read {source} and cite the relevant page", None
    )

    assert candidates == [source.resolve()]
    assert missing == []


def test_document_agent_ranking_drops_path_words_for_zip_content(tmp_path, monkeypatch):
    from agents import document_agent

    archive = tmp_path / "Pathoma Slides.zip"
    archive.write_bytes(b"zip")
    monkeypatch.setattr(
        document_agent,
        "execute_tool",
        lambda name, args: (
            "ZIP: Pathoma Slides.zip\n\n"
            "--- Page 1 ---\nChapter title\n\n"
            "--- Page 3 ---\nGastroschisis\n"
        ) if name == "read_document" else "",
    )

    evidence, chunks = document_agent._prepare_evidence(
        f"Find Gastroschisis in {archive}", None
    )

    assert any("Gastroschisis" in chunk["text"] for chunk in chunks)
    assert "LOCATOR: page 3" in evidence
