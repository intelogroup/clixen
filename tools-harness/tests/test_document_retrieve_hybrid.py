import json


def test_document_retrieve_hybrid_adds_existing_index_hits(tmp_path, monkeypatch):
    from tools import document_retrieve

    monkeypatch.setattr(document_retrieve, "read_document", lambda *args, **kwargs: "local lexical text")
    monkeypatch.setattr(
        "tools.fulltext_search.fulltext_search",
        lambda *args, **kwargs: f"File: {tmp_path / 'indexed.txt'} (chunk 2, score 4.0)\nindexed semantic fact",
    )
    monkeypatch.setattr(
        "tools.semantic_files.semantic_file_search",
        lambda *args, **kwargs: "No files indexed yet. Use index_directory first.",
    )
    (tmp_path / "local.txt").write_text("local lexical text")

    result = json.loads(document_retrieve.document_retrieve("fact", str(tmp_path), limit=4))

    assert any("indexed semantic fact" in item["text"] for item in result)
