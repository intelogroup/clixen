"""The orb's own workspace: one directory, and nothing outside it.

Why this exists: until now the orb had no file capability at all, and the tools
that did write (write_file, bash_exec) were gated by `path_policy`, whose write
roots include the repo, the whole dev-projects parent directory and Downloads.
For a shipped app that is the wrong shape — a user's Documents are theirs, and
the agent's files should be the agent's.

So: one root, resolved from the app, fail-closed, and every path argument is
resolved relative to it and re-checked after symlink resolution.

Root resolution, in order:
    $CLIXEN_WORKSPACE                     the app set it (vault/workspace)
    $CLIXEN_DATA_DIR/../workspace         the sidecar's env is vault/data
    ~/Library/Application Support/Clixen/workspace     dev, no app running

Fail-closed rule: when the path came from the app it must be an *actually
mounted* vault. Writing to `<mountpoint>/...` while the image is detached
succeeds against the bare directory underneath and the file vanishes when the
vault is mounted later — a silent data-loss bug, so an unmounted vault is an
error, not a fallback.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

MARKER = ".clixen-vault"
MAX_WRITE_BYTES = 1_000_000
_TRASH = ".trash"


class WorkspaceError(RuntimeError):
    """A path or the workspace itself is not usable. Never silently ignored."""


def _root_and_source() -> tuple[Path, str]:
    explicit = os.environ.get("CLIXEN_WORKSPACE")
    if explicit:
        return Path(explicit).expanduser(), "app"
    data_dir = os.environ.get("CLIXEN_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser().parent / "workspace", "sidecar"
    return Path.home() / "Library" / "Application Support" / "Clixen" / "workspace", "dev"


def _is_mounted_vault(root: Path) -> bool:
    """True when there is a real vault behind this path."""
    for probe in (root, root.parent):
        if (probe / MARKER).exists():
            return True
    return os.path.ismount(str(root.parent)) or (root.exists() and os.path.ismount(str(root)))


def workspace_root(create: bool = True) -> Path:
    root, source = _root_and_source()
    if source != "dev":
        # The app told us where its vault is. If it is not mounted, stop: see the
        # module docstring for the data-loss this prevents.
        if not _is_mounted_vault(root):
            raise WorkspaceError(
                f"the Clixen vault is not mounted at {root.parent} — nothing was read or written. "
                "Mounted vaults contain a .clixen-vault marker."
            )
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _root() -> Path:
    """The workspace root, fully resolved.

    Comparing a resolved target against an unresolved root fails on macOS for any
    root under a symlink (/tmp -> /private/tmp): every legitimate path then reads
    as an escape. Caught by the live probe, not by the suite, which used
    already-resolved tmp dirs.
    """
    return workspace_root().resolve()


def resolve(rel: str, *, must_exist: bool = False) -> Path:
    """Map a workspace-relative path to a real path inside the workspace.

    Rejects absolute paths, `~`, NUL bytes and `..` outright (so the message is
    about what the caller did), then re-checks containment *after* resolving
    symlinks (so a link pointing out of the tree is caught too).
    """
    if not isinstance(rel, str):
        raise WorkspaceError("path must be a string")
    if "\x00" in rel:
        raise WorkspaceError("path contains a NUL byte")
    raw = rel.strip()
    root = _root()
    if raw in ("", ".", "./"):
        return root
    if raw.startswith("/") or raw.startswith("~"):
        raise WorkspaceError(f"'{rel}' is absolute; workspace paths are relative (e.g. notes/today.md)")
    if ".." in Path(raw).parts:
        raise WorkspaceError(f"'{rel}' contains '..'; the workspace cannot be left")
    target = (root / raw).resolve()
    if target != root and not target.is_relative_to(root):
        raise WorkspaceError(f"'{rel}' resolves outside the workspace ({target})")
    if must_exist and not target.exists():
        raise WorkspaceError(f"no such file in the workspace: {raw}")
    return target


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(_root())) or "."
    except ValueError:
        return str(path)


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024.0
    return f"{size:.1f}GB"


def list_entries(rel: str = "") -> str:
    """One directory, newest first. Directory names end with '/'."""
    target = resolve(rel)
    if not target.exists():
        return f"[error] no such folder in the workspace: {rel or '.'}"
    if target.is_file():
        return f"[error] {rel} is a file, not a folder — use workspace_read"
    entries = sorted(
        (p for p in target.iterdir() if p.name != _TRASH),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not entries:
        return f"Workspace folder '{_rel(target)}' is empty."
    lines = [f"Workspace folder '{_rel(target)}' ({len(entries)} entries, newest first):"]
    for p in entries:
        try:
            st = p.stat()
        except OSError:
            continue
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        if p.is_dir():
            lines.append(f"  {p.name}/  dir  {when}")
        else:
            lines.append(f"  {p.name}  {_human(st.st_size)}  {when}")
    return "\n".join(lines)


def read_text(rel: str, offset: int = 1, limit: int = 200) -> str:
    """Paged read, same shape as read_file but confined to the workspace."""
    target = resolve(rel, must_exist=True)
    if target.is_dir():
        return f"[error] {rel} is a folder — use workspace_list"
    try:
        raw = target.read_bytes()[: MAX_WRITE_BYTES * 4].decode("utf-8", errors="replace")
    except OSError as exc:
        return f"[error] could not read {rel}: {exc}"
    lines = raw.splitlines()
    start = max(0, int(offset) - 1)
    chunk = lines[start : start + max(1, int(limit))]
    end = start + len(chunk)
    header = f"File: {_rel(target)}  ({len(lines)} lines total, {_human(target.stat().st_size)})"
    nav = f"\n[lines {start + 1}-{end} of {len(lines)} — next page: offset={end + 1}]" if end < len(lines) else ""
    numbered = "\n".join(f"{start + i + 1:4}: {ln}" for i, ln in enumerate(chunk))
    return f"{header}\n\n{numbered}{nav}"


def write_text(rel: str, content: str) -> str:
    """Atomic write: temp file in the same directory, then rename.

    Overwriting snapshots the old version into `.trash/` first, so a mistaken
    write is recoverable and `delete` never destroys anything outright.
    """
    if not isinstance(content, str):
        raise WorkspaceError("content must be a string")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_WRITE_BYTES:
        return f"[error] {len(encoded)} bytes is over the {MAX_WRITE_BYTES // 1000}KB workspace write limit"
    target = resolve(rel)
    if target == _root():
        return "[error] give a file name, not the workspace root"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists():
        if target.is_dir():
            return f"[error] {rel} is a folder"
        _snapshot(target)
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    return f"Written {lines} lines ({len(encoded)} bytes) to {_rel(target)}"


def delete(rel: str) -> str:
    """Move into .trash/ — recoverable, never unlink."""
    target = resolve(rel, must_exist=True)
    if target == _root():
        return "[error] refusing to delete the workspace root"
    dest = _trash_dir() / f"{int(time.time())}-{target.name}"
    try:
        shutil.move(str(target), str(dest))
    except OSError as exc:
        return f"[error] could not move {rel} to trash: {exc}"
    return f"Moved {_rel(target)} to .trash/{dest.name} (recoverable)"


def _trash_dir() -> Path:
    trash = _root() / _TRASH
    trash.mkdir(parents=True, exist_ok=True, mode=0o700)
    return trash


def _snapshot(target: Path) -> None:
    try:
        shutil.copy2(target, _trash_dir() / f"{int(time.time())}-pre-{target.name}")
    except OSError:
        pass  # a failed backup must not block the write; the write is atomic either way


def screenshots_dir() -> Path:
    """Where screenshots go: inside the workspace, so they are encrypted at rest."""
    path = _root() / "screenshots"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def prune_screenshots(keep: int = 50, max_age_days: int = 7) -> int:
    """Remove the oldest screenshots past `keep` or `max_age_days`. Returns count."""
    folder = screenshots_dir()
    shots = sorted(folder.glob("shot-*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for index, shot in enumerate(shots):
        try:
            if index < keep and shot.stat().st_mtime >= cutoff:
                continue
            shot.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def guarded(fn, **kwargs) -> str:
    """Call a workspace function, turning a WorkspaceError into a tool-error string.

    A tool must never raise at the model: it either did the thing or it explains
    why not, and both are strings the model can act on.
    """
    try:
        return fn(**kwargs)
    except WorkspaceError as exc:
        return f"[error] {exc}"


WORKSPACE_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workspace_list",
        "description": "List your own workspace folder (newest first). Your files live here, not in the user's folders.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Folder relative to the workspace root; omit for the top level"},
            },
        },
    },
}

WORKSPACE_READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workspace_read",
        "description": "Read a text file from your workspace, paged by line like read_file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root, e.g. notes/today.md"},
                "offset": {"type": "integer", "description": "First line to return (1-based)", "default": 1},
                "limit": {"type": "integer", "description": "How many lines", "default": 200},
            },
            "required": ["path"],
        },
    },
}

WORKSPACE_WRITE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workspace_write",
        "description": "Create or overwrite a text file in your workspace. Overwrites keep the old version in .trash.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root; no absolute paths"},
                "content": {"type": "string", "description": "Full file content (up to 1MB)"},
            },
            "required": ["path", "content"],
        },
    },
}

WORKSPACE_DELETE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workspace_delete",
        "description": "Move a workspace file to .trash (recoverable). Only when the user asked to delete it.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root"},
            },
            "required": ["path"],
        },
    },
}
