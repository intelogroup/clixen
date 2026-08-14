from tools.document_lifecycle import (
    delete_document,
    forget_document_index,
    quarantine_document,
    request_delete_document,
)
from tools.registry import execute_confirmed


def test_quarantine_keeps_source_and_removes_derived_state(tmp_path, monkeypatch):
    source = tmp_path / "bad.txt"
    source.write_text("unreliable")
    removed = []
    monkeypatch.setattr("tools.document_lifecycle._remove_derived", lambda path: removed.append(path))
    statuses = []
    monkeypatch.setattr("tools.document_manifest.set_status", lambda path, status: statuses.append((path, status)))

    result = quarantine_document(str(source))

    assert source.exists()
    assert "Quarantined" in result
    assert removed == [source.resolve()]
    assert statuses[0][1] == "quarantined"


def test_forget_keeps_source_and_forgets_manifest(tmp_path, monkeypatch):
    source = tmp_path / "old.txt"
    source.write_text("old")
    removed = []
    forgotten = []
    monkeypatch.setattr("tools.document_lifecycle._remove_derived", lambda path: removed.append(path))
    monkeypatch.setattr("tools.document_manifest.forget", lambda path: forgotten.append(path))

    result = forget_document_index(str(source))

    assert source.exists()
    assert "source retained" in result
    assert removed == [source.resolve()]
    assert forgotten == [source.resolve()]


def test_delete_only_requests_confirmation_until_approved(tmp_path, monkeypatch):
    source = tmp_path / "delete.txt"
    source.write_text("delete me")
    monkeypatch.setattr("tools.document_lifecycle.request_confirmation", lambda *args: "tok-delete")

    pending = request_delete_document(str(source))

    assert source.exists()
    assert "Nothing was deleted" in pending
    monkeypatch.setattr("tools.document_lifecycle._remove_derived", lambda path: None)
    monkeypatch.setattr("tools.document_manifest.forget", lambda path: None)
    assert "Deleted" in delete_document(str(source))
    assert not source.exists()


def test_delete_confirmation_approval_executes_lifecycle_delete(tmp_path, monkeypatch):
    source = tmp_path / "approved.txt"
    source.write_text("remove")
    monkeypatch.setattr("tools.document_lifecycle._remove_derived", lambda path: None)
    monkeypatch.setattr("tools.document_manifest.forget", lambda path: None)
    token = request_delete_document(str(source)).split("Token: ", 1)[1].rstrip(".")

    result = execute_confirmed(token, approved=True)

    assert "Deleted" in result
    assert not source.exists()
