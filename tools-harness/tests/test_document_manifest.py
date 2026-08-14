def test_manifest_skips_unchanged_file_and_detects_content_change(tmp_path, monkeypatch):
    from tools import document_manifest

    db_path = tmp_path / "manifest.db"
    monkeypatch.setattr(document_manifest, "_DB_PATH", db_path)
    path = tmp_path / "memo.txt"
    path.write_text("version one")

    assert document_manifest.needs_index(path) is True
    document_manifest.mark_indexed(path)
    assert document_manifest.needs_index(path) is False

    path.write_text("version two")
    assert document_manifest.needs_index(path) is True


def test_manifest_sync_candidates_returns_only_changed_files(tmp_path, monkeypatch):
    from tools import document_manifest

    monkeypatch.setattr(document_manifest, "_DB_PATH", tmp_path / "manifest.db")
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first")
    second.write_text("second")
    document_manifest.mark_indexed(first)

    candidates = document_manifest.sync_candidates([first, second])

    assert candidates == [second.resolve()]


def test_manifest_keeps_old_version_and_only_current_content_is_active(tmp_path, monkeypatch):
    from tools import document_manifest

    monkeypatch.setattr(document_manifest, "_DB_PATH", tmp_path / "manifest.db")
    path = tmp_path / "memo.txt"
    path.write_text("version one")
    first = document_manifest.fingerprint(path)
    document_manifest.mark_indexed(path)

    path.write_text("version two")
    second = document_manifest.fingerprint(path)
    assert first != second
    assert document_manifest.is_active_version(path) is False
    document_manifest.mark_indexed(path)

    recorded = {item["fingerprint"]: item["status"] for item in document_manifest.versions(path)}
    assert recorded[first] == "superseded"
    assert recorded[second] == "active"
    assert document_manifest.is_active_version(path) is True
