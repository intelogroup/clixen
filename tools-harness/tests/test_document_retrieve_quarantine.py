import json


def test_document_retrieve_excludes_quarantined_source(tmp_path, monkeypatch):
    from tools.document_retrieve import document_retrieve

    source = tmp_path / "bad.txt"
    source.write_text("secret false fact")
    monkeypatch.setattr("tools.document_manifest.is_quarantined", lambda path: True)

    result = json.loads(document_retrieve("secret", str(tmp_path), search_mode="lexical"))

    assert result == []
