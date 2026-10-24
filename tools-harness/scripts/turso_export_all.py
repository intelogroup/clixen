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

# SQLite files that hold real state. Order is irrelevant; discovery is by glob.
DEFAULT_GLOBS = [
    "store/*.db",
    "data/*.sqlite",
    "jobs/*.db",
    "reminders.db",
    "tools/_tool_failures.db",
    "data/tsdata/catalog.db",
]

# Excluded from discovery unless named explicitly with --db. catalog.db is a
# local time-series catalog (2.7M rows of `observations`): bulk measurements,
# not agent state. Exporting it would dominate runtime and replica size.
DEFAULT_EXCLUDES = {
    "data/tsdata/catalog.db",
}

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


def discover_dbs(explicit: list[str], include_science_scout: bool) -> list[Path]:
    if explicit:
        candidates = [Path(p) if Path(p).is_absolute() else ROOT / p for p in explicit]
    else:
        candidates = []
        for pattern in DEFAULT_GLOBS:
            candidates.extend(sorted(ROOT.glob(pattern)))
    out: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve()
        if path in seen or not path.is_file() or path.stat().st_size < 4096:
            continue
        if path.name == "science_scout.db" and not include_science_scout:
            continue  # already mirrored live by store/science_scout_store.py
        try:
            rel = path.relative_to(ROOT).as_posix()
        except ValueError:
            rel = path.name
        if rel in DEFAULT_EXCLUDES:
            continue
        seen.add(path)
        out.append(path)
    return out


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


def export_db(db_path: Path, batch_size: int, truncate: bool, verify_only: bool) -> bool:
    prefix = target_prefix(db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        table_map, _indexes, statements, skipped = plan_schema(conn, prefix)
        tables = [t for t in _names(conn, "table") if t in table_map]
        print(f"\n== {db_path.name}  ({len(tables)} tables, prefix {prefix!r})")
        if skipped:
            print(f"   skipped (not mappable): {skipped}")
        if not verify_only:
            turso_client.execute_batch([(s, ()) for s in statements])
        ok = True
        for table in tables:
            target = table_map[table]
            if not verify_only:
                if truncate:
                    turso_client.execute(f'DELETE FROM "{target}"')
                rows, sha = export_table(conn, table, target, batch_size)
                _manifest_upsert(db_path.name, table, target, rows, sha)
                print(f"   {table:24s} -> {target:44s} {rows:6d} rows")
            try:
                match, local_n, remote_n, local_h, remote_h = verify_table(conn, table, target)
            except turso_client.TursoError as exc:
                print(f"   {table:24s} -> {target:44s} UNREADABLE: {exc}")
                ok = False
                continue
            ok &= match
            detail = "MATCH" if match else f"MISMATCH {local_h[:8]}/{remote_h[:8]}"
            print(f"      verify local={local_n} remote={remote_n} {detail}")
        return ok
    finally:
        conn.close()


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
    args = ap.parse_args()

    if args.truncate and not args.yes:
        print("Refusing --truncate without --yes", file=sys.stderr)
        return 1
    if not turso_client.enabled():
        print(f"Turso not configured: {turso_client.disabled_reason()}", file=sys.stderr)
        return 1

    dbs = discover_dbs(args.db or [], args.include_science_scout)
    print(f"target: {turso_client._db_url()}")
    print(f"{turso_client.ping()}  |  {len(dbs)} database(s) to export")
    if not dbs:
        print("nothing to export", file=sys.stderr)
        return 1

    try:
        if not args.verify_only:
            turso_client.execute(MANIFEST_DDL)
        ok = True
        for db_path in dbs:
            if args.dry_run:
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
                continue
            ok &= export_db(db_path, max(1, args.batch_size), args.truncate, args.verify_only)
        print("\nRESULT:", "all tables match" if ok else "MISMATCH/errors — see above")
        return 0 if ok else 1
    except turso_client.TursoError as exc:
        print(f"Turso error: {exc} (code={exc.code})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
