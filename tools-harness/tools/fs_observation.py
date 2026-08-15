"""
Read-before-write CAS guard for file tools — ported from deepseek-harness's
fs-observation-policy (packages/fs/fs-observation-policy).

Tracks, per request, the last version (mtime_ns, size) the model observed for
each path via read_file. write_file/edit_file/edit_file_fuzzy/append_file
check that version against the file's current on-disk state before mutating:

- never observed, and the file exists on disk        -> reject (read it first)
- observed absent, but the file now exists            -> reject (read it first)
- observed present, but the on-disk version changed    -> reject (stale, re-read)
- observed present, version matches, or file is new    -> allowed

Scoped by a ContextVar (like _PROJECT_ROOT in filesystem.py) so concurrent
requests (web + telegram + whatsapp can interleave) don't share state, and
copy_context() carries it into tool-executor threads the same way.
"""

import contextvars as _cv
from pathlib import Path

_Version = tuple[int, int]  # (mtime_ns, size)
# path str -> ("present", mtime_ns, size) | ("absent",)
_Observation = tuple[str, int, int] | tuple[str]

_OBSERVED: "_cv.ContextVar[dict[str, _Observation]]" = _cv.ContextVar("_FS_OBSERVED")


def _store() -> dict[str, _Observation]:
    try:
        return _OBSERVED.get()
    except LookupError:
        d: dict[str, _Observation] = {}
        _OBSERVED.set(d)
        return d


def _disk_version(p: Path) -> _Version | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def observe(p: Path) -> None:
    """Record the file's current on-disk state as observed (call after any successful read/write/edit)."""
    key = str(p)
    version = _disk_version(p)
    _store()[key] = ("present", *version) if version is not None else ("absent",)


def check_write(p: Path) -> str | None:
    """Guard for write_file (blind create is fine; blind overwrite of an unread file is not). Returns an error string, or None if the write may proceed."""
    key = str(p)
    prior = _store().get(key)
    current = _disk_version(p)

    if prior is None:
        if current is not None:
            return f"file exists but was never read in this session — read_file({p}) first, or use edit_file to modify it"
        return None  # unobserved + doesn't exist -> blind create is fine

    if prior[0] == "absent":
        if current is not None:
            return f"file now exists on disk but was absent when last observed — read_file({p}) again before writing"
        return None

    # prior == ("present", mtime_ns, size)
    if current is None:
        return f"file no longer exists on disk (deleted since last observed) — read_file({p}) again before writing"
    if (prior[1], prior[2]) != current:
        return f"file changed on disk since it was last read — read_file({p}) again before writing"
    return None


def check_edit(p: Path) -> str | None:
    """Guard for edit_file/edit_file_fuzzy/append_file — these always require a prior observation (no blind edit of an absent or unread file). Returns an error string, or None if the edit may proceed."""
    key = str(p)
    prior = _store().get(key)
    if prior is None:
        return f"edit requires reading {p} first — call read_file({p}) before editing"
    if prior[0] == "absent":
        return f"cannot edit {p}: not found when last observed"

    current = _disk_version(p)
    if current is None:
        return f"file no longer exists on disk (deleted since last observed) — read_file({p}) again before editing"
    if (prior[1], prior[2]) != current:
        return f"file changed on disk since it was last read — read_file({p}) again before editing"
    return None
