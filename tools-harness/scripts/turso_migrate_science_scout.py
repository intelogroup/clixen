#!/usr/bin/env python3
"""Migrate the local science-scout SQLite ledger into the Turso replica.

Local `store/science_scout.db` stays the source of truth; this copies every row
once so the replica starts from the full history instead of only from whatever
the scout writes after 2026-10-24 (when `store/turso_client.py` mirroring went
live). Safe to re-run: every insert is `INSERT OR REPLACE`, and explicit ids are
preserved on the AUTOINCREMENT tables (`paper_claim_links`, `subagent_prompts`,
`suppressed_alerts`) so the two sides stay row-identical.

Usage:
  python3 scripts/turso_migrate_science_scout.py --dry-run
  python3 scripts/turso_migrate_science_scout.py
  python3 scripts/turso_migrate_science_scout.py --verify-only
  python3 scripts/turso_migrate_science_scout.py --table raw_papers --batch-size 100

Exit codes: 0 = success + verification matched, 1 = verification mismatch/error.

Credentials come from tools-harness/.env (TURSO_DATABASE_URL) plus the Keychain
(TURSO_AUTH_TOKEN) via store/turso_client.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store import science_scout_store as local  # noqa: E402
from store import turso_client  # noqa: E402

# (table, order column) — order only affects determinism of progress/verify.
TABLES: list[tuple[str, str]] = [
    ("meta", "key"),
    ("query_usage", "query"),
    ("niche_perf", "niche"),
    ("raw_papers", "id"),
    ("claims", "id"),
    ("paper_claim_links", "id"),
    ("subagent_prompts", "id"),
    ("suppressed_alerts", "id"),
]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _read_local(conn: sqlite3.Connection, table: str, order: str) -> tuple[list[str], list[tuple]]:
    cols = _columns(conn, table)
    rows = conn.execute(
        f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order}"
    ).fetchall()
    return cols, [tuple(r) for r in rows]


def _norm(value):
    """REAL drifts 1 ULP between engines; compare at 12 significant digits."""
    if isinstance(value, float):
        return float(f"{value:.12g}")
    return value


def _canonical_hash(cols: list[str], rows) -> tuple[str, int]:
    """Order-independent digest over every value in the table."""
    digest = hashlib.sha256()
    lines = []
    for row in rows:
        values = row if isinstance(row, (list, tuple)) else [row.get(c) for c in cols]
        lines.append(json.dumps([_norm(v) for v in values], sort_keys=True, ensure_ascii=False, default=str))
    for line in sorted(lines):
        digest.update(line.encode())
    return digest.hexdigest(), len(lines)


def _read_remote(table: str, cols: list[str], order: str, page: int = 500) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        batch = turso_client.query(
            f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order} LIMIT ? OFFSET ?",
            (page, offset),
        )
        rows.extend(batch)
        if len(batch) < page:
            return rows
        offset += page


def migrate_table(
    conn: sqlite3.Connection, table: str, order: str, batch_size: int, truncate: bool = False,
) -> int:
    """Copy one table with multi-row INSERTs.

    One round trip per `batch_size` rows as a *single* statement
    (`VALUES (..),(..),..`) rather than N statements: Turso's per-request
    overhead dominates, and 250 single-row statements measured ~13s/250 rows
    versus <1s for the multi-row form. Row count per statement is capped by
    SQLite's variable limit (~32k), so wide tables get smaller chunks.
    """
    cols, rows = _read_local(conn, table, order)
    print(f"[{table:18s}] local={len(rows)} rows")
    if truncate:
        turso_client.execute(f"DELETE FROM {table}")
        print(f"[{table:18s}] remote table cleared")
    if not rows:
        return 0

    row_arity = max(1, len(cols))
    per_statement = max(1, min(batch_size, 30000 // row_arity))
    row_placeholders = "(" + ", ".join("?" for _ in cols) + ")"
    header = f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES "
    done = 0
    for start in range(0, len(rows), per_statement):
        chunk = rows[start:start + per_statement]
        sql = header + ", ".join(row_placeholders for _ in chunk)
        params = [value for row in chunk for value in row]
        turso_client.execute(sql, params)
        done += len(chunk)
        print(f"[{table:18s}] pushed {done}/{len(rows)}", flush=True)
    return done


def verify_table(conn: sqlite3.Connection, table: str, order: str) -> bool:
    cols, local_rows = _read_local(conn, table, order)
    remote_rows = _read_remote(table, cols, order)
    local_hash, local_n = _canonical_hash(cols, local_rows)
    remote_hash, remote_n = _canonical_hash(cols, remote_rows)
    ok = local_hash == remote_hash and local_n == remote_n
    print(
        f"[{table:18s}] local={local_n:6d} remote={remote_n:6d} "
        f"{'MATCH' if ok else 'MISMATCH'} {local_hash[:12]} / {remote_hash[:12]}"
    )
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", action="append", choices=[t for t, _ in TABLES],
                    help="limit to one table (repeatable)")
    ap.add_argument("--batch-size", type=int, default=200, help="rows per HTTP round trip (default 200)")
    ap.add_argument("--dry-run", action="store_true", help="report row counts only, write nothing")
    ap.add_argument("--verify-only", action="store_true", help="skip writes, compare local vs remote")
    ap.add_argument("--truncate", action="store_true", help="DELETE remote rows before copying (destructive)")
    ap.add_argument("--yes", action="store_true", help="required with --truncate")
    args = ap.parse_args()

    if args.truncate and not args.yes:
        print("Refusing --truncate without --yes", file=sys.stderr)
        return 1
    if not turso_client.enabled():
        print(f"Turso not configured: {turso_client.disabled_reason()}", file=sys.stderr)
        return 1
    print(f"target: {turso_client._db_url()}  |  {turso_client.ping()}")

    selected = [(t, o) for t, o in TABLES if not args.table or t in args.table]
    conn = sqlite3.connect(f"file:{local.DB_PATH}?mode=ro", uri=True)

    try:
        if args.dry_run:
            for table, order in selected:
                _, rows = _read_local(conn, table, order)
                print(f"[{table:18s}] would push {len(rows)} rows")
            return 0

        if not args.verify_only:
            for table, order in selected:
                migrate_table(conn, table, order, max(1, args.batch_size), truncate=args.truncate)
            print("\ncopy complete — verifying")

        ok = True
        for table, order in selected:
            ok &= verify_table(conn, table, order)
        print("\nRESULT:", "all tables match" if ok else "MISMATCH — see above")
        return 0 if ok else 1
    except turso_client.TursoError as exc:
        print(f"Turso error: {exc} (code={exc.code})", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

