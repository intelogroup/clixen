from pathlib import Path


def test_session_scratch_isolated_and_traversal_safe(tmp_path):
    from tools.document_workspace import safe_artifact_path, session_dir

    first = session_dir("chat-a", str(tmp_path))
    second = session_dir("chat-b", str(tmp_path))

    assert first != second
    assert first.stat().st_mode & 0o777 == 0o700
    assert safe_artifact_path("chat-a", "preview.pdf", str(tmp_path)).parent == first

    try:
        safe_artifact_path("chat-a", "../escape.pdf", str(tmp_path))
    except PermissionError:
        pass
    else:
        raise AssertionError("scratch traversal was accepted")


def test_scratchpad_is_bounded_and_visible(tmp_path):
    from tools.document_workspace import add_note, read_notes, scratchpad_block

    for index in range(40):
        add_note("chat-a", f"note-{index}", str(tmp_path))

    notes = read_notes("chat-a", str(tmp_path))
    assert len(notes) == 32
    assert notes[0] == "note-8"
    assert "Working notes" in scratchpad_block("chat-a", str(tmp_path))
