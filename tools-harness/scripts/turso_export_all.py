#!/usr/bin/env python3
"""Export every local SQLite store into the Turso replica.

Scope (2026-10-24): science scout already mirrors live into Turso via
`store/turso_client.py`; every *other* local database is exported here as a
namespaced snapshot — table `world_monitor__findings` etc. — in the same
`science-scout` Turso database.

Why namespacing instead of one Turso DB per local file: Turso's free plan caps
database count, and the ledger questions that matter ("what did the scouts
find, and when") are cross-store. A `<db>__<table>` prefix keeps colliding
tables (`meta`, `query_usage`, `subagent_prompts` exist in several stores)
unambiguous.

This is a re-runnable snapshot, not live dual-write: `INSERT OR REPLACE` plus
`--truncate` makes a refresh exact, and every run records row counts + a
canonical per-table SHA-256 in `_export_manifest` so drift is detectable.

Usage:
  python3 scripts/turso_export_all.py --dry-run
  python3 scripts/turso_export_all.py
  python3 scripts/turso_export_all.py --verify-only
  python3 scripts/turso_export_all.py --db store/world_monitor.db --truncate --yes
  python3 scripts/turso_export_all.py --include-science-scout   # re-snapshot too

Exit codes: 0 = exported + verified, 1 = verification mismatch or transport error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from store import turso_client  # noqa: E402
from store.db_inventory import discover_dbs  # noqa: E402

MANIFEST_DDL = """
CREATE TABLE IF NOT EXISTS _export_manifest (
    source_db    TEXT NOT NULL,
    source_table TEXT NOT NULL,
    target_table TEXT NOT NULL,
    rows         INTEGER NOT NULL,
    sha256       TEXT NOT NULL,
    exported_at  TEXT NOT NULL,
    PRIMARY KEY (source_db, source_table)
)
"""

_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\"[^\"]+\"|\[[^\]]+\]|`[^`]+`|\w+)\s*\(",
    re.I,
)
_CREATE_INDEX_RE = re.compile(
    r"^\s*CREATE\s+(UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(\"[^\"]+\"|\[[^\]]+\]|`[^`]+`|\w+)\s+ON\s+(\"[^\"]+\"|\[[^\]]+\]|`[^`]+`|\w+)",
    re.I,
)
_REFERENCES_RE = re.compile(r"\bREFERENCES\s+(\"[^\"]+\"|\[[^\]]+\]|`[^`]+`|\w+)", re.I)


def _unquote(identifier: str) -> str:
    return identifier.strip().strip('"').strip("`").strip("[]")


def namespace(name: str) -> str:
    """filesystem name -> safe SQL identifier fragment."""
    return re.sub(r"[^0-9a-zA-Z_]+", "_", name).strip("_").lower()


def target_prefix(db_path: Path) -> str:
    return namespace(db_path.stem) + "__"


def load_manifest() -> dict[tuple[str, str], dict]:
    """(source_db, source_table) -> last exported rows/hash, for incremental skips."""
    try:
        rows = turso_client.query(
            "select source_db, source_table, rows, sha256 from _export_manifest"
        )
    except turso_client.TursoError:
        return {}
    return {(r["source_db"], r["source_table"]): r for r in rows}


def ensure_columns(conn: sqlite3.Connection, table: str, target: str) -> list[str]:
    """Add columns that exist locally but not yet remotely.

    `CREATE TABLE IF NOT EXISTS` is a no-op once the table exists, so a store
    that gains a column would otherwise fail every nightly INSERT with
    "table has no column named X" and silently stop replicating. ALTER TABLE
    ADD COLUMN can't carry NOT NULL without a default, so those are added
    nullable and the next copy fills them.
    """
    local = {r[1]: r for r in conn.execute(f'PRAGMA table_info("{table}")')}
    try:
        remote = {r["name"] for r in turso_client.query(f'PRAGMA table_info("{target}")')}
    except turso_client.TursoError:
        return []  # table absent — the CREATE statements will make it
    if not remote:
        return []
    added: list[str] = []
    for name, row in local.items():
        if name in remote:
            continue
        decl = (row[2] or "").strip()
        ddl = f'ALTER TABLE "{target}" ADD COLUMN "{name}"'
        if decl:
            ddl += f" {decl}"
        if row[4] is not None:  # dflt_value
            ddl += f" DEFAULT {row[4]}"
        turso_client.execute(ddl)
        added.append(name)
    return added


def _is_current(
    conn: sqlite3.Connection, db_name: str, table: str, target: str,
    manifest: dict[tuple[str, str], dict],
) -> bool:
    """True when the manifest hash matches local rows AND the remote count agrees."""
    entry = manifest.get((db_name, table))
    if not entry:
        return False
    cols = _columns(conn, table)
    local_hash, local_n = canonical_hash(cols, _read_local(conn, table, cols))
    if entry.get("rows") != local_n or entry.get("sha256") != local_hash:
        return False
    try:
        remote_n = turso_client.query(f'select count(*) as c from "{target}"')[0]["c"]
    except turso_client.TursoError:
        return False
    return remote_n == local_n



def _objects(conn: sqlite3.Connection):
    return conn.execute(
        "select type, name, tbl_name, sql from sqlite_master "
        "where sql is not null and name not like 'sqlite_%' "
        "order by case type when 'table' then 0 else 1 end, rowid"
    ).fetchall()


def _names(conn: sqlite3.Connection, kind: str) -> list[str]:
    return [
        r[0] for r in conn.execute(
            f"select name from sqlite_master where type='{kind}' and name not like 'sqlite_%' order by name"
        )
    ]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def rewrite_ddl(ddl: str, table_map: dict[str, str], index_map: dict[str, str]) -> str | None:
    """Retarget a stored CREATE statement at this database's namespace.

    Returns None for objects we cannot map (e.g. virtual tables), which the
    caller skips with a warning. Foreign keys are rewritten to the namespaced
    table so cross-table constraints inside one source DB still resolve.
    """
    sql = ddl.strip().rstrip(";")
    table = _CREATE_TABLE_RE.match(sql)
    if table:
        new = table_map.get(_unquote(table.group(1)))
        if new is None:
            return None
        out = f"CREATE TABLE IF NOT EXISTS {new}(" + sql[table.end():]
        return _REFERENCES_RE.sub(
            lambda m: "REFERENCES " + table_map.get(_unquote(m.group(1)), _unquote(m.group(1))),
            out,
        )
    index = _CREATE_INDEX_RE.match(sql)
    if index:
        unique = "UNIQUE " if index.group(1) else ""
        new_index = index_map.get(_unquote(index.group(2)))
        new_table = table_map.get(_unquote(index.group(3)))
        if new_index is None or new_table is None:
            return None
        return f"CREATE {unique}INDEX IF NOT EXISTS {new_index} ON {new_table}" + sql[index.end():]
    return None


def plan_schema(conn: sqlite3.Connection, prefix: str):
    """(table_map, index_map, create_statements, skipped_objects)."""
    table_map = {t: prefix + namespace(t) for t in _names(conn, "table")}
    index_map = {
        i: prefix + namespace(i)
        for (type_, i, tbl, _sql) in _objects(conn)
        if type_ == "index" and tbl in table_map
    }
    statements: list[str] = []
    skipped: list[str] = []
    for type_, name, _tbl, sql in _objects(conn):
        if type_ not in ("table", "index"):
            skipped.append(f"{type_}:{name}")
            continue
        rewritten = rewrite_ddl(sql, table_map, index_map)
        if rewritten is None:
            skipped.append(f"{type_}:{name}")
        else:
            statements.append(rewritten)
    return table_map, index_map, statements, skipped


def _norm(value):
    """Normalize for hashing.

    REAL values can drift by 1 ULP between local SQLite and Turso (observed:
    1786588681.3877919 vs .387792, ~2e-7 s on epoch timestamps) — the engines
    are not bit-identical on the REAL round trip. Compare at 12 significant
    digits so verification reports real drift, not float noise.
    """
    if isinstance(value, float):
        return float(f"{value:.12g}")
    return value


def canonical_hash(cols: list[str], rows) -> tuple[str, int]:
    digest = hashlib.sha256()
    lines = []
    for row in rows:
        values = row if isinstance(row, (list, tuple)) else [row.get(c) for c in cols]
        lines.append(json.dumps([_norm(v) for v in values], sort_keys=True, ensure_ascii=False, default=str))
    for line in sorted(lines):
        digest.update(line.encode())
    return digest.hexdigest(), len(lines)


def _read_local(conn: sqlite3.Connection, table: str, cols: list[str]) -> list[tuple]:
    rows = conn.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{table}"').fetchall()
    return [tuple(r) for r in rows]


def _read_remote(target: str, cols: list[str], page: int = 500) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        batch = turso_client.query(
            f'SELECT {", ".join(cols)} FROM "{target}" LIMIT ? OFFSET ?', (page, offset)
        )
        rows.extend(batch)
        if len(batch) < page:
            return rows
        offset += page


def export_table(
    conn: sqlite3.Connection, table: str, target: str, batch_size: int,
) -> tuple[int, str]:
    cols = _columns(conn, table)
    rows = _read_local(conn, table, cols)
    if not rows:
        return 0, canonical_hash(cols, rows)[0]
    arity = max(1, len(cols))
    per_statement = max(1, min(batch_size, 30000 // arity))
    placeholder_row = "(" + ", ".join("?" for _ in cols) + ")"
    header = f'INSERT OR REPLACE INTO "{target}" ({", ".join(cols)}) VALUES '
    for start in range(0, len(rows), per_statement):
        chunk = rows[start:start + per_statement]
        sql = header + ", ".join(placeholder_row for _ in chunk)
        turso_client.execute(sql, [value for row in chunk for value in row])
    return len(rows), canonical_hash(cols, rows)[0]


def verify_table(conn: sqlite3.Connection, table: str, target: str) -> tuple[bool, int, int, str, str]:
    cols = _columns(conn, table)
    local_hash, local_n = canonical_hash(cols, _read_local(conn, table, cols))
    remote_hash, remote_n = canonical_hash(cols, _read_remote(target, cols))
    return (local_hash == remote_hash and local_n == remote_n), local_n, remote_n, local_hash, remote_hash


def _manifest_upsert(source_db: str, source_table: str, target: str, rows: int, sha: str) -> None:
    turso_client.execute(
        "INSERT OR REPLACE INTO _export_manifest "
        "(source_db, source_table, target_table, rows, sha256, exported_at) VALUES (?, ?, ?, ?, ?, ?)",
        (source_db, source_table, target, rows, sha, datetime.now(timezone.utc).isoformat()),
    )


def export_db(
    db_path: Path,
    batch_size: int,
    truncate: bool,
    verify_only: bool,
    incremental: bool = False,
    manifest: dict[tuple[str, str], dict] | None = None,
    quiet: bool = False,
) -> dict:
    """Copy one database. Returns per-db stats; per-table problems don't raise."""
    prefix = target_prefix(db_path)
    manifest = manifest or {}
    stats = {
        "db": db_path.name, "tables": 0, "rows": 0, "skipped": 0,
        "mismatch": 0, "unverified": 0, "raced": 0, "rebuilt": 0,
        "added_columns": {},
    }
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        table_map, _indexes, statements, skipped = plan_schema(conn, prefix)
        tables = [t for t in _names(conn, "table") if t in table_map]
        if not quiet:
            print(f"\n== {db_path.name}  ({len(tables)} tables, prefix {prefix!r})")
            if skipped:
                print(f"   skipped (not mappable): {skipped}")
        if not verify_only:
            turso_client.execute_batch([(s, ()) for s in statements])
        for table in tables:
            target = table_map[table]
            stats["tables"] += 1
            exported_sha = None
            exported_rows = None
            if not verify_only:
                if incremental and not truncate and _is_current(
                    conn, db_path.name, table, target, manifest
                ):
                    stats["skipped"] += 1
                    if not quiet:
                        print(f"   {table:24s} unchanged — skipped")
                    continue
                added = ensure_columns(conn, table, target)
                if added:
                    stats["added_columns"][f"{db_path.name}:{table}"] = added
                    if not quiet:
                        print(f"   {table:24s} +columns {added}")
                if truncate:
                    turso_client.execute(f'DELETE FROM "{target}"')
                exported_rows, exported_sha = export_table(conn, table, target, batch_size)
                _manifest_upsert(db_path.name, table, target, exported_rows, exported_sha)
                stats["rows"] += exported_rows
                if not quiet:
                    print(f"   {table:24s} -> {target:44s} {exported_rows:6d} rows")

            # A live table (event_log, workflow_instances, notifications) can be
            # written while we copy it. Re-hash locally: if it moved, the remote
            # difference is our own race, not drift — don't fail the run for it.
            if exported_sha is not None:
                cols = _columns(conn, table)
                now_sha, now_n = canonical_hash(cols, _read_local(conn, table, cols))
                if (now_sha, now_n) != (exported_sha, exported_rows):
                    stats["raced"] += 1
                    if not quiet:
                        print(f"   {table:24s} changed mid-copy — verify skipped")
                    continue

            try:
                match, local_n, remote_n, local_h, remote_h = verify_table(conn, table, target)
            except turso_client.TursoError as exc:
                stats["unverified"] += 1
                if not quiet:
                    print(f"   {table:24s} -> {target:44s} UNREADABLE: {exc}")
                continue

            # Capped/evicting tables (event_log keeps 2000, oldest evicted) leave
            # ghost rows behind after an incremental INSERT OR REPLACE. Rebuild
            # that table once instead of reporting drift forever.
            if (not match and not verify_only and not truncate
                    and local_n != remote_n):
                turso_client.execute(f'DELETE FROM "{target}"')
                export_table(conn, table, target, batch_size)
                stats["rebuilt"] += 1
                if not quiet:
                    print(f"   {table:24s} rebuild (remote had {remote_n}, local {local_n})")
                try:
                    match, local_n, remote_n, local_h, remote_h = verify_table(conn, table, target)
                except turso_client.TursoError:
                    match = False

            if not match:
                stats["mismatch"] += 1
                if not quiet:
                    print(f"      verify local={local_n} remote={remote_n} "
                          f"MISMATCH {local_h[:8]}/{remote_h[:8]}")
            elif not quiet:
                print(f"      verify local={local_n} remote={remote_n} MATCH")
        return stats
    finally:
        conn.close()


def run_export(
    dbs,
    *,
    batch_size: int = 300,
    truncate: bool = False,
    verify_only: bool = False,
    incremental: bool = False,
    quiet: bool = False,
) -> dict:
    """Aggregate export over many databases — shared by the CLI and the nightly job."""
    if not turso_client.enabled():
        return {"success": False, "error": f"Turso disabled: {turso_client.disabled_reason()}"}
    manifest = {} if verify_only else load_manifest()
    if not verify_only:
        turso_client.execute(MANIFEST_DDL)

    totals = {
        "success": True, "databases": 0, "tables": 0, "rows": 0,
        "skipped": 0, "mismatch": 0, "unverified": 0, "raced": 0, "rebuilt": 0,
        "added_columns": {}, "errors": [],
    }
    for db_path in dbs:
        try:
            stats = export_db(
                db_path, batch_size, truncate, verify_only, incremental, manifest, quiet
            )
        except Exception as exc:  # noqa: BLE001 — one bad DB must not stop the rest
            totals["errors"].append(f"{db_path.name}: {type(exc).__name__}: {exc}")
            continue
        totals["databases"] += 1
        for key in ("tables", "rows", "skipped", "mismatch", "unverified", "raced", "rebuilt"):
            totals[key] += stats[key]
        totals["added_columns"].update(stats["added_columns"])

    if totals["errors"] or totals["mismatch"] or totals["unverified"]:
        totals["success"] = False
        totals["error"] = (
            totals["errors"][0] if totals["errors"] else
            f"{totals['mismatch']} table(s) mismatched, {totals['unverified']} unreadable"
        )
    return totals



def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", action="append", help="export only this db file (repeatable)")
    ap.add_argument("--batch-size", type=int, default=300, help="rows per INSERT statement")
    ap.add_argument("--dry-run", action="store_true", help="list what would be exported")
    ap.add_argument("--verify-only", action="store_true", help="compare only, write nothing")
    ap.add_argument("--truncate", action="store_true", help="DELETE each target table first")
    ap.add_argument("--yes", action="store_true", help="required with --truncate")
    ap.add_argument("--include-science-scout", action="store_true",
                    help="also snapshot store/science_scout.db (already mirrored live)")
    ap.add_argument("--incremental", action="store_true",
                    help="skip tables whose manifest hash matches local rows")
    args = ap.parse_args()

    if args.truncate and not args.yes:
        print("Refusing --truncate without --yes", file=sys.stderr)
        return 1
    if not turso_client.enabled():
        print(f"Turso not configured: {turso_client.disabled_reason()}", file=sys.stderr)
        return 1

    dbs = discover_dbs(
        args.db or [],
        include_live_mirrored=args.include_science_scout,
    )
    print(f"target: {turso_client._db_url()}")
    print(f"{turso_client.ping()}  |  {len(dbs)} database(s) to export")
    if not dbs:
        print("nothing to export", file=sys.stderr)
        return 1

    if args.dry_run:
        for db_path in dbs:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                table_map, _, statements, _ = plan_schema(conn, target_prefix(db_path))
                print(f"\n== {db_path.name}: {len(statements)} objects, "
                      f"{len(table_map)} tables -> prefix {target_prefix(db_path)!r}")
                for t, target in sorted(table_map.items()):
                    n = conn.execute(f'select count(*) from "{t}"').fetchone()[0]
                    print(f"   {t:24s} -> {target:44s} {n:6d} rows")
            finally:
                conn.close()
        return 0

    try:
        totals = run_export(
            dbs,
            batch_size=max(1, args.batch_size),
            truncate=args.truncate,
            verify_only=args.verify_only,
            incremental=args.incremental,
        )
    except turso_client.TursoError as exc:
        print(f"Turso error: {exc} (code={exc.code})", file=sys.stderr)
        return 1

    for line in totals.get("errors", []):
        print(f"error: {line}", file=sys.stderr)
    print(
        f"\nRESULT: databases={totals['databases']} tables={totals['tables']} "
        f"rows={totals['rows']} skipped={totals['skipped']} "
        f"rebuilt={totals['rebuilt']} raced={totals['raced']} "
        f"mismatch={totals['mismatch']} unverified={totals['unverified']}"
    )
    if totals.get("added_columns"):
        print("added columns:", totals["added_columns"])
    print("STATUS:", "OK" if totals["success"] else f"FAILED — {totals.get('error', '')}")
    return 0 if totals["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
