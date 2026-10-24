"""
Inventory of the local SQLite stores that get backed up / replicated.

One definition, two consumers:
  * `scripts/turso_export_all.py` — pushes them into the Turso replica
  * `scripts/db_snapshot.py`      — VACUUM INTO snapshots for real restores
  * `jobs/handlers/nightly_backup.py` — runs both nightly

Discovery is by glob so a new store is picked up without touching code. Anything
excluded here is excluded for a documented reason, not by accident.
"""

from __future__ import annotations

from pathlib import Path

TOOLS_HARNESS = Path(__file__).resolve().parent.parent

# Files that hold real state.
DEFAULT_GLOBS = [
    "store/*.db",
    "data/*.sqlite",
    "jobs/*.db",
    "reminders.db",
    "tools/_tool_failures.db",
    "data/tsdata/catalog.db",
]

# Excluded unless named explicitly. catalog.db is a local time-series catalog
# (2.7M rows of `observations`): bulk measurements, not agent state, and it
# dominates both snapshot time and replica size.
DEFAULT_EXCLUDES = {
    "data/tsdata/catalog.db",
}

# Live-mirrored stores: they write to Turso through store/turso_client.py, so
# they never need a manual push. Their science_scout.db is skipped by the
# exporter for that reason; world_monitor/reddit_intel still get snapshotted
# locally (the replica is not a backup).
LIVE_MIRRORED_DBS = {"science_scout.db"}


def discover_dbs(
    explicit: list[str] | None = None,
    *,
    root: Path | None = None,
    include_excludes: bool = False,
    include_live_mirrored: bool = True,
    min_size: int = 4096,
) -> list[Path]:
    """Resolve the SQLite files to process, de-duplicated and sorted."""
    root = Path(root or TOOLS_HARNESS)
    if explicit:
        candidates = [Path(p) if Path(p).is_absolute() else root / p for p in explicit]
    else:
        candidates = []
        for pattern in DEFAULT_GLOBS:
            candidates.extend(sorted(root.glob(pattern)))

    out: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve()
        if path in seen or not path.is_file() or path.stat().st_size < min_size:
            continue
        try:
            rel = path.relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = path.name
        if not include_excludes and rel in DEFAULT_EXCLUDES:
            continue
        if not include_live_mirrored and path.name in LIVE_MIRRORED_DBS:
            continue
        seen.add(path)
        out.append(path)
    return out
