import json


def test_document_retrieve_is_workspace_scoped_and_ranked(tmp_path):
    from tools.document_retrieve import document_retrieve

    (tmp_path / "contract.txt").write_text("Termination notice is 30 days.")
    (tmp_path / "other.txt").write_text("Office snacks are coffee and tea.")
    result = json.loads(document_retrieve("termination notice", str(tmp_path), limit=1))

    assert len(result) == 1
    assert result[0]["source"].endswith("contract.txt")
    assert "30 days" in result[0]["text"]


def test_document_retrieve_rejects_escape_glob(tmp_path):
    from tools.document_retrieve import document_retrieve

    result = document_retrieve("secret", str(tmp_path), path_glob="../*.txt")

    assert result.startswith("[error] path_glob")


def test_document_retrieve_filters_to_exact_relative_glob(tmp_path):
    from tools.document_retrieve import document_retrieve

    (tmp_path / "keep.txt").write_text("target fact")
    (tmp_path / "skip.txt").write_text("target fact from another file")

    result = json.loads(document_retrieve("target fact", str(tmp_path), path_glob="keep.txt"))

    assert result
    assert all(item["source"].endswith("keep.txt") for item in result)
