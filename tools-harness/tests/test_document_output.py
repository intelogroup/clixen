def test_verify_export_rejects_missing_or_empty_files(tmp_path):
    from tools.document_output import verify_export

    missing = verify_export(str(tmp_path / "missing.pdf"))
    assert not missing["ok"]

    empty = tmp_path / "empty.md"
    empty.write_text("")
    result = verify_export(str(empty))
    assert not result["ok"]


def test_verify_export_accepts_nonempty_markdown(tmp_path):
    from tools.document_output import verify_export

    output = tmp_path / "result.md"
    output.write_text("# Summary\nApproved.")
    result = verify_export(str(output))

    assert result["ok"]
    assert result["format"] == "md"


def test_restore_version_replaces_file_and_archives_current_state(tmp_path, monkeypatch):
    from tools.document_output import archive_existing, list_versions, restore_version

    version_root = tmp_path / "private-versions"
    monkeypatch.setattr("tools.document_output.data_dir", lambda: version_root)
    target = tmp_path / "draft.md"
    target.write_text("before")
    backup = archive_existing(str(target))
    target.write_text("after")

    result = restore_version(str(target), str(backup))

    assert result["ok"] is True
    assert result["path"] == str(target.resolve())
    assert target.read_text() == "before"
    versions = list_versions(str(target), limit=10)
    assert len(versions) == 2
    assert any(row["backup"] == backup for row in versions)


def test_restore_version_rejects_snapshot_for_another_source(tmp_path, monkeypatch):
    from tools.document_output import archive_existing, restore_version

    monkeypatch.setattr("tools.document_output.data_dir", lambda: tmp_path / "private-versions")
    source = tmp_path / "source.md"
    other = tmp_path / "other.md"
    source.write_text("source")
    other.write_text("other")
    backup = archive_existing(str(source))

    result = restore_version(str(other), str(backup))

    assert result["ok"] is False
    assert "snapshot is not registered" in result["error"]
    assert other.read_text() == "other"


def test_delete_versions_removes_private_snapshots(tmp_path, monkeypatch):
    from tools.document_output import archive_existing, delete_versions, list_versions

    monkeypatch.setattr("tools.document_output.data_dir", lambda: tmp_path / "private-versions")
    target = tmp_path / "draft.md"
    target.write_text("before")
    archive_existing(str(target))

    assert list_versions(str(target))
    assert delete_versions(str(target)) >= 2
    assert list_versions(str(target)) == []
