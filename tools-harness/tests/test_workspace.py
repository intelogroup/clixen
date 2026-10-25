"""The workspace is the only directory the orb can touch, and it must stay that way.

Stdlib-only (no PIL/AppKit/numpy) so it runs in the `voice` CI job alongside the
injection-fence tests: this is a security boundary, not a convenience wrapper.

Every escape test asserts two things: the call fails *and* nothing was created
outside the root. A rejection that still writes somewhere is worse than no check.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import workspace as ws  # noqa: E402


@pytest.fixture()
def root(tmp_path, monkeypatch):
    """A dev-style workspace: no app env, so no vault marker is required."""
    monkeypatch.delenv("CLIXEN_WORKSPACE", raising=False)
    monkeypatch.delenv("CLIXEN_DATA_DIR", raising=False)
    monkeypatch.setenv("CLIXEN_HOME", str(tmp_path / "home"))
    # CLIXEN_HOME only affects path_policy; point the workspace at tmp directly.
    monkeypatch.setenv("CLIXEN_WORKSPACE", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / ws.MARKER).write_text("1")  # pretend it is mounted
    return tmp_path / "workspace"


@pytest.mark.parametrize(
    "bad",
    ["../escape.txt", "/etc/passwd", "~/notes.md", "a/../../b.txt", "notes/../../../x"],
)
def test_paths_cannot_leave_the_workspace(root, bad):
    with pytest.raises(ws.WorkspaceError):
        ws.resolve(bad)
    # and the tool surface reports it instead of raising at the model
    out = ws.guarded(ws.write_text, rel=bad, content="x")
    assert out.startswith("[error]")
    assert not (root.parent / "escape.txt").exists()
    assert not (root.parent / "b.txt").exists()


def test_nul_byte_is_rejected(root):
    with pytest.raises(ws.WorkspaceError):
        ws.resolve("notes\x00.md")


def test_symlink_pointing_out_is_rejected(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ws.WorkspaceError):
        ws.resolve("link/secret.txt")
    assert ws.guarded(ws.write_text, rel="link/secret.txt", content="x").startswith("[error]")
    assert not (outside / "secret.txt").exists()


def test_unmounted_vault_refuses_and_writes_nothing(tmp_path, monkeypatch):
    """The data-loss case: <vault>/workspace with no vault behind it."""
    bare = tmp_path / "mnt"
    (bare / "workspace").mkdir(parents=True)
    monkeypatch.setenv("CLIXEN_WORKSPACE", str(bare / "workspace"))
    out = ws.guarded(ws.write_text, rel="note.md", content="hello")
    assert out.startswith("[error]") and "not mounted" in out
    assert list((bare / "workspace").iterdir()) == []


def test_write_read_roundtrip_and_permissions(root):
    assert ws.write_text("notes/today.md", "line one\nline two\n").startswith("Written 2 lines")
    target = root / "notes" / "today.md"
    assert target.read_text() == "line one\nline two\n"
    assert oct(target.stat().st_mode)[-3:] == "600"
    out = ws.read_text("notes/today.md")
    assert "1: line one" in out and "2: line two" in out


def test_overwrite_keeps_a_snapshot_and_delete_is_recoverable(root):
    ws.write_text("a.txt", "first")
    ws.write_text("a.txt", "second")
    assert (root / "a.txt").read_text() == "second"
    snapshots = list((root / ".trash").glob("*-pre-a.txt"))
    assert snapshots and snapshots[0].read_text() == "first"

    moved = ws.delete("a.txt")
    assert "recoverable" in moved
    assert not (root / "a.txt").exists()
    assert (root / ".trash").is_dir() and any((root / ".trash").iterdir())


def test_size_limit(root):
    out = ws.write_text("big.txt", "x" * (ws.MAX_WRITE_BYTES + 1))
    assert out.startswith("[error]") and "limit" in out
    assert not (root / "big.txt").exists()


def test_list_is_newest_first_and_skips_trash(root):
    ws.write_text("old.txt", "1")
    os.utime(root / "old.txt", (1_000_000, 1_000_000))
    ws.write_text("new.txt", "2")
    listing = ws.list_entries("")
    assert "new.txt" in listing and "old.txt" in listing
    assert listing.index("new.txt") < listing.index("old.txt")
    assert ".trash" not in listing


def test_screenshots_go_inside_the_workspace(root):
    ws.write_text("keep.txt", "x")
    assert ws.screenshots_dir() == root / "screenshots"
    for i in range(3):
        (root / "screenshots" / f"shot-{i}.png").write_bytes(b"png")
    removed = ws.prune_screenshots(keep=1)
    assert removed == 2 and len(list((root / "screenshots").glob("shot-*.png"))) == 1


def test_write_to_the_root_itself_is_refused(root):
    assert ws.write_text("", "x").startswith("[error]")
    assert ws.write_text(".", "x").startswith("[error]")
