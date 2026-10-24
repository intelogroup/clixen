"""MirroredConnection: local writes replicate to Turso on commit, never before.

What would break the two scout stores if this regressed:
  * a rolled-back transaction leaking into the replica,
  * un-namespaced writes colliding with science scout's `meta`/`query_usage`,
  * a Turso failure killing a scan that had already committed locally.
"""
from __future__ import annotations

import pytest

from store import dbclose
from store import turso_client
from store import world_monitor_store as wm


@pytest.fixture
def batches(monkeypatch):
    calls: list[list[tuple[str, tuple]]] = []

    def fake(statements):
        calls.append(list(statements))
        return True

    monkeypatch.setattr(turso_client, "mirror_batch", fake)
    return calls


def _conn(tmp_path, namespace="ns__"):
    return dbclose.connect_mirrored(str(tmp_path / "t.db"), namespace)


def test_insert_flushes_once_on_commit(tmp_path, batches):
    conn = _conn(tmp_path)
    conn.executescript("CREATE TABLE t (v TEXT)")
    conn.commit()
    batches.clear()

    conn.execute("INSERT INTO t (v) VALUES (?)", ("hello",))
    assert batches == [], "must not mirror before commit"
    conn.commit()
    assert batches == [[("INSERT INTO ns__t (v) VALUES (?)", ("hello",))]]
    conn.close()


def test_rollback_discards_pending(tmp_path, batches):
    conn = _conn(tmp_path)
    conn.executescript("CREATE TABLE t (v TEXT)")
    conn.commit()
    batches.clear()

    conn.execute("INSERT INTO t (v) VALUES ('x')")
    conn.rollback()
    assert batches == []
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
    conn.close()


def test_ddl_select_and_pragma_are_not_mirrored(tmp_path, batches):
    conn = _conn(tmp_path)
    conn.executescript("CREATE TABLE a (x INTEGER); CREATE TABLE b (y INTEGER);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_a ON a (x)")
    conn.execute("SELECT 1")
    conn.execute("PRAGMA user_version")
    conn.commit()
    assert batches == []
    conn.close()


def test_table_names_are_namespaced(tmp_path, batches):
    conn = _conn(tmp_path, "wm__")
    conn.executescript(
        "CREATE TABLE meta (k TEXT, v TEXT);"
        "CREATE TABLE findings (a INTEGER);"
        "CREATE TABLE watched_subreddits (name TEXT, status TEXT);"
        "CREATE TABLE raw_scans (scanned_at TEXT);"
    )
    conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", ("k", "v"))
    conn.execute('INSERT INTO "findings" (a) VALUES (?)', (1,))
    conn.execute("UPDATE watched_subreddits SET status='dropped' WHERE name = ?", ("python",))
    conn.execute("DELETE FROM raw_scans WHERE scanned_at < ?", ("2020",))
    conn.commit()

    mirrored = [sql for batch in batches for sql, _ in batch]
    assert any(sql.startswith("INSERT OR REPLACE INTO wm__meta") for sql in mirrored)
    assert any(sql.startswith('INSERT INTO "wm__findings"') for sql in mirrored)
    assert any(sql.startswith("UPDATE wm__watched_subreddits") for sql in mirrored)
    assert any(sql.startswith("DELETE FROM wm__raw_scans") for sql in mirrored)
    assert not any("INTO findings" in sql for sql in mirrored)
    conn.close()


def test_executemany_mirrors_every_row(tmp_path, batches):
    conn = _conn(tmp_path)
    conn.executescript("CREATE TABLE t (v TEXT)")
    conn.commit()
    batches.clear()

    conn.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",)])
    conn.commit()
    assert batches == [[
        ("INSERT INTO ns__t (v) VALUES (?)", ("a",)),
        ("INSERT INTO ns__t (v) VALUES (?)", ("b",)),
    ]]
    conn.close()


def test_mirror_failure_never_breaks_the_local_write(tmp_path, monkeypatch):
    def boom(statements):
        raise RuntimeError("turso down")

    monkeypatch.setattr(turso_client, "mirror_batch", boom)
    conn = _conn(tmp_path)
    conn.executescript("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('kept')")
    conn.commit()  # must not raise
    assert conn.execute("SELECT v FROM t").fetchone()[0] == "kept"
    conn.close()


def test_world_monitor_store_writes_are_mirrored(tmp_path, monkeypatch, batches):
    monkeypatch.setattr(wm, "DB_PATH", tmp_path / "wm.db")
    wm.init()
    batches.clear()

    wm.save_raw_scan("reddit", "raw text")
    assert batches, "save_raw_scan should reach the replica"
    sql, params = batches[-1][0]
    assert sql.startswith("INSERT INTO world_monitor__raw_scans")
    assert params[1] == "reddit" and params[2] == "raw text"
