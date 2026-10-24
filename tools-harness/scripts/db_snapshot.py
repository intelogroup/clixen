#!/usr/bin/env python3
"""Consistent local snapshots of every SQLite store — the actual restore story.

Turso is a queryable replica, not a backup: it keeps no history and lags by a
day. This writes one consistent copy of each store per run:

    data/backups/<YYYY-MM-DD>/<store>.db

Uses the sqlite3 **backup API** (not `shutil.copy`): it runs inside a read
transaction, so the copy is consistent while the daemons keep writing in WAL
mode. The copy is then `VACUUM`ed so it is compact and opens without the
original's free-page bloat. Rotation keeps the newest N day-directories.

Usage:
  python3 scripts/db_snapshot.py --dry-run
  python3 scripts/db_snapshot.py --keep 7
  python3 scripts/db_snapshot.py --db store/science_scout.db --keep 3
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from store.db_inventory import discover_dbs  # noqa: E402

BACKUP_ROOT = ROOT / "data" / "backups"


def snapshot_db(db_path: Path, out_dir: Path) -> tuple[Path, int]:
    """Write a consistent copy of db_path into out_dir. Returns (path, bytes)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{db_path.stem}.db"
    if dest.exists():
        dest.unlink()

    # Read-only source: the backup API needs only a shared read lock, so live
    # daemon writes (WAL) don't block it and it never mutates the source.
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src.backup(dest_conn)
            # The backup inherits the source's journal_mode=WAL, which would make
            # the snapshot depend on -wal/-shm sidecars when reopened. Switch the
            # copy back to a plain rollback journal so it is one self-contained
            # file, then compact it.
            dest_conn.execute("PRAGMA journal_mode=DELETE")
            dest_conn.execute("VACUUM")
        finally:
            dest_conn.close()
    finally:
        src.close()
    for sidecar in (dest.with_name(dest.name + "-wal"), dest.with_name(dest.name + "-shm")):
        if sidecar.exists():
            sidecar.unlink()
    return dest, dest.stat().st_size


def verify_snapshot(path: Path) -> tuple[bool, str]:
    """A backup you never opened is not a backup."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            tables = conn.execute(
                "select count(*) from sqlite_master where type='table'"
            ).fetchone()[0]
            return (row[0] == "ok"), f"integrity={row[0]} tables={tables}"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"{type(exc).__name__}: {exc}"


def rotate(root: Path, keep: int) -> list[str]:
    """Delete all but the newest `keep` day-directories."""
    if not root.is_dir():
        return []
    days = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    removed = []
    for old in days[keep:]:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old.name)
    return removed


def run_snapshots(
    dbs, *, out_root: Path = BACKUP_ROOT, keep: int = 7, verify: bool = True,
) -> dict:
    """Snapshot every db. Never raises; returns stats for the nightly job."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = out_root / stamp
    stats = {
        "success": True, "date": stamp, "dir": str(out_dir),
        "databases": 0, "bytes": 0, "failed": [], "unverified": [],
        "removed": [],
    }
    for db_path in dbs:
        try:
            dest, size = snapshot_db(db_path, out_dir)
        except Exception as exc:  # noqa: BLE001 — one store must not stop the rest
            stats["failed"].append(f"{db_path.name}: {type(exc).__name__}: {exc}")
            continue
        stats["databases"] += 1
        stats["bytes"] += size
        if verify:
            ok, detail = verify_snapshot(dest)
            if not ok:
                stats["unverified"].append(f"{db_path.name}: {detail}")
    try:
        stats["removed"] = rotate(out_root, keep)
    except OSError as exc:
        stats["failed"].append(f"rotation: {exc}")
    if stats["failed"] or stats["unverified"]:
        stats["success"] = False
        stats["error"] = (stats["failed"] or stats["unverified"])[0]
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", action="append", help="snapshot only this db (repeatable)")
    ap.add_argument("--dir", default=str(BACKUP_ROOT), help="backup root directory")
    ap.add_argument("--keep", type=int, default=7, help="day-directories to keep (default 7)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be snapshotted")
    ap.add_argument("--no-verify", action="store_true", help="skip PRAGMA integrity_check")
    args = ap.parse_args()

    dbs = discover_dbs(args.db or [])
    if not dbs:
        print("nothing to snapshot", file=sys.stderr)
        return 1

    if args.dry_run:
        total = sum(p.stat().st_size for p in dbs)
        print(f"{len(dbs)} databases, {total / 1_048_576:.1f} MB source")
        for path in dbs:
            print(f"   {path.name:28s} {path.stat().st_size:>10,d} bytes")
        return 0

    stats = run_snapshots(dbs, out_root=Path(args.dir), keep=max(1, args.keep),
                          verify=not args.no_verify)
    print(f"snapshots -> {stats['dir']}")
    print(f"RESULT: databases={stats['databases']} bytes={stats['bytes']} "
          f"removed={stats['removed']}")
    for line in stats["failed"] + stats["unverified"]:
        print(f"  problem: {line}", file=sys.stderr)
    print("STATUS:", "OK" if stats["success"] else f"FAILED — {stats.get('error', '')}")
    return 0 if stats["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
