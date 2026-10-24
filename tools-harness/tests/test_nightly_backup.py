"""Nightly backup: local snapshots + incremental Turso refresh.

Pins the parts that fail silently:
  * inventory (catalog.db excluded, science_scout included for snapshots)
  * snapshot fidelity (the copy must actually open and hold every row)
  * rotation keeps the newest N day-directories, not the oldest
  * incremental skip logic (hash + remote count) and its schema-drift guard
  * the handler's contract: never raise, and surface failures as success=False
    so jobs/worker.py telegrams instead of drifting quietly.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobs.handlers import nightly_backup
from store import db_inventory

HARNESS = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"t_{name}", HARNESS / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


snap = _load("db_snapshot")
export = _load("turso_export_all")


@pytest.fixture
def store_db(tmp_path):
    path = tmp_path / "demo.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])
    conn.commit()
    conn.close()
    return path


# ── inventory ──────────────────────────────────────────────────────────────


def test_inventory_excludes_catalog_by_default():
    names = {p.name for p in db_inventory.discover_dbs([])}
    assert "science_scout.db" in names, "snapshots must cover the live-mirrored store too"
    assert "catalog.db" not in names
    assert "world_monitor.db" in names


def test_inventory_can_skip_live_mirrored():
    names = {p.name for p in db_inventory.discover_dbs([], include_live_mirrored=False)}
    assert "science_scout.db" not in names
    assert "world_monitor.db" in names


def test_inventory_explicit_path_bypasses_excludes(store_db):
    assert db_inventory.discover_dbs([str(store_db)]) == [store_db.resolve()]


# ── snapshots ──────────────────────────────────────────────────────────────


def test_snapshot_is_readable_and_complete(store_db, tmp_path):
    dest, size = snap.snapshot_db(store_db, tmp_path / "out")
    assert dest.exists() and size > 0
    conn = sqlite3.connect(str(dest))
    try:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    finally:
        conn.close()
    ok, detail = snap.verify_snapshot(dest)
    assert ok and "integrity=ok" in detail
    # A snapshot must be one self-contained file — no -wal/-shm sidecars.
    assert not Path(str(dest) + "-wal").exists()
    assert not Path(str(dest) + "-shm").exists()


def test_rotation_keeps_newest_dirs(tmp_path):
    root = tmp_path / "backups"
    for day in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"):
        (root / day).mkdir(parents=True)
    removed = snap.rotate(root, keep=2)
    assert removed == ["2026-10-02", "2026-10-01"]
    assert sorted(p.name for p in root.iterdir()) == ["2026-10-03", "2026-10-04"]


def test_run_snapshots_reports_stats(store_db, tmp_path):
    stats = snap.run_snapshots([store_db], out_root=tmp_path / "b", keep=3)
    assert stats["success"] is True
    assert stats["databases"] == 1 and stats["bytes"] > 0
    assert Path(stats["dir"]).is_dir()


# ── incremental refresh ────────────────────────────────────────────────────


def test_is_current_true_only_when_hash_and_count_match(store_db, monkeypatch):
    conn = sqlite3.connect(f"file:{store_db}?mode=ro", uri=True)
    try:
        cols = export._columns(conn, "t")
        digest, n = export.canonical_hash(cols, export._read_local(conn, "t", cols))
        manifest = {("demo.db", "t"): {"rows": n, "sha256": digest}}

        monkeypatch.setattr(export.turso_client, "query", lambda sql, params=(): [{"c": n}])
        assert export._is_current(conn, "demo.db", "t", "ns__t", manifest) is True

        monkeypatch.setattr(export.turso_client, "query", lambda sql, params=(): [{"c": n + 1}])
        assert export._is_current(conn, "demo.db", "t", "ns__t", manifest) is False
        assert export._is_current(conn, "demo.db", "t", "ns__t", {}) is False
        stale = {("demo.db", "t"): {"rows": n, "sha256": "deadbeef"}}
        assert export._is_current(conn, "demo.db", "t", "ns__t", stale) is False
    finally:
        conn.close()


def test_ensure_columns_noop_when_schema_matches(store_db, monkeypatch):
    conn = sqlite3.connect(f"file:{store_db}?mode=ro", uri=True)
    try:
        monkeypatch.setattr(
            export.turso_client, "query", lambda sql, params=(): [{"name": "id"}, {"name": "v"}]
        )
        executed: list[str] = []
        monkeypatch.setattr(
            export.turso_client, "execute", lambda sql, params=(): executed.append(sql)
        )
        assert export.ensure_columns(conn, "t", "ns__t") == []
        assert executed == []
    finally:
        conn.close()


def test_ensure_columns_alters_when_local_has_extra(tmp_path, monkeypatch):
    db = tmp_path / "drift.db"
    c = sqlite3.connect(str(db))
    c.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT, extra INTEGER DEFAULT 0);")
    c.commit()
    c.close()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        monkeypatch.setattr(
            export.turso_client, "query", lambda sql, params=(): [{"name": "id"}, {"name": "v"}]
        )
        executed: list[str] = []
        monkeypatch.setattr(
            export.turso_client, "execute", lambda sql, params=(): executed.append(sql)
        )
        assert export.ensure_columns(conn, "t", "ns__t") == ["extra"]
        assert executed == ['ALTER TABLE "ns__t" ADD COLUMN "extra" INTEGER DEFAULT 0']
    finally:
        conn.close()


def test_export_db_rebuilds_capped_table_on_count_mismatch(store_db, monkeypatch):
    """Ghost rows from an evicting table must trigger one DELETE+recopy, not a permanent failure."""
    calls = {"n": 0}

    def fake_verify(conn, table, target):
        calls["n"] += 1
        if calls["n"] == 1:
            return (False, 3, 5, "aaaa", "bbbb")
        return (True, 3, 3, "aaaa", "aaaa")

    executed: list[str] = []
    monkeypatch.setattr(export, "verify_table", fake_verify)

    def fake_export(conn, table, target, bs):
        cols = export._columns(conn, table)
        digest, n = export.canonical_hash(cols, export._read_local(conn, table, cols))
        return n, digest  # a real digest, so the mid-run race guard doesn't fire

    monkeypatch.setattr(export, "export_table", fake_export)
    monkeypatch.setattr(export, "ensure_columns", lambda conn, t, tgt: [])
    monkeypatch.setattr(export, "_manifest_upsert", lambda *a, **k: None)
    monkeypatch.setattr(export.turso_client, "execute", lambda sql, params=(): executed.append(sql))
    monkeypatch.setattr(export.turso_client, "execute_batch", lambda stmts, timeout=None: [])

    stats = export.export_db(store_db, 100, False, False, quiet=True)

    assert stats["rebuilt"] == 1
    assert stats["mismatch"] == 0
    assert executed == ['DELETE FROM "demo__t"']


def test_export_db_skips_verification_when_the_table_moves(store_db, monkeypatch):
    """A live table written mid-copy is a race, not drift — must not fail the run."""

    def fake_export(conn, table, target, bs):
        writer = sqlite3.connect(str(store_db))
        writer.execute("INSERT INTO t (v) VALUES ('late')")
        writer.commit()
        writer.close()
        return 3, "hash-before-the-late-row"

    monkeypatch.setattr(export, "export_table", fake_export)
    monkeypatch.setattr(export, "ensure_columns", lambda conn, t, tgt: [])
    monkeypatch.setattr(export, "_manifest_upsert", lambda *a, **k: None)
    monkeypatch.setattr(export.turso_client, "execute_batch", lambda stmts, timeout=None: [])

    def must_not_verify(*args, **kwargs):
        raise AssertionError("verification must be skipped for a raced table")

    monkeypatch.setattr(export, "verify_table", must_not_verify)

    stats = export.export_db(store_db, 100, False, False, quiet=True)

    assert stats["raced"] == 1
    assert stats["mismatch"] == 0 and stats["unverified"] == 0


# ── handler contract ───────────────────────────────────────────────────────



def _fake_script(**fns):
    return SimpleNamespace(**fns)


def _ok_export(**over):
    base = {
        "success": True, "databases": 21, "tables": 41, "rows": 0, "skipped": 41,
        "mismatch": 0, "unverified": 0, "errors": [], "added_columns": {},
    }
    base.update(over)
    return base


def test_handler_runs_both_steps(monkeypatch):
    monkeypatch.setattr(
        nightly_backup, "_load_script",
        lambda name: _fake_script(
            run_snapshots=lambda dbs, keep=7: {
                "success": True, "date": "2026-10-24", "dir": "/tmp/b",
                "databases": 21, "bytes": 1024, "removed": [],
            },
            run_export=lambda dbs, **kw: _ok_export(),
        ),
    )
    result = nightly_backup.handle({"config": {"keep_snapshots": 5}})
    assert result["success"] is True
    assert result["snapshots"]["databases"] == 21
    assert result["turso"]["skipped"] == 41


def test_handler_surfaces_snapshot_failure(monkeypatch):
    monkeypatch.setattr(
        nightly_backup, "_load_script",
        lambda name: _fake_script(
            run_snapshots=lambda dbs, keep=7: {"success": False, "error": "disk full"},
            run_export=lambda dbs, **kw: _ok_export(),
        ),
    )
    result = nightly_backup.handle({})
    assert result["success"] is False
    assert "disk full" in result["error"]


def test_handler_surfaces_turso_failure(monkeypatch):
    monkeypatch.setattr(
        nightly_backup, "_load_script",
        lambda name: _fake_script(
            run_snapshots=lambda dbs, keep=7: {"success": True},
            run_export=lambda dbs, **kw: _ok_export(success=False, error="circuit open"),
        ),
    )
    result = nightly_backup.handle({})
    assert result["success"] is False
    assert "circuit open" in result["error"]


def test_handler_never_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("exploded")

    monkeypatch.setattr(nightly_backup, "_load_script", boom)
    result = nightly_backup.handle({"config": {}})
    assert result["success"] is False
    assert "crashed" in result["error"]


def test_handler_can_skip_steps(monkeypatch):
    calls: list[str] = []

    def snap_step(dbs, keep=7):
        calls.append("snap")
        return {"success": True}

    def turso_step(dbs, **kw):
        calls.append("turso")
        return _ok_export()

    monkeypatch.setattr(
        nightly_backup, "_load_script",
        lambda name: _fake_script(run_snapshots=snap_step, run_export=turso_step),
    )
    nightly_backup.handle({"config": {"skip_turso": True}})
    assert calls == ["snap"]

