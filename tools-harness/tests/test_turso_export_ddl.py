"""DDL namespacing for the full-store Turso export.

The exporter never hand-writes INSERT/CREATE — it replays each local object's
`sqlite_master.sql` retargeted at a `<db>__` namespace. If that rewrite
regresses, rows land in the wrong table or constraints are silently dropped, so
pin the transformations here.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "turso_export_all.py"


def _load():
    spec = importlib.util.spec_from_file_location("turso_export_all", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ex = _load()


def test_namespace_and_prefix():
    assert ex.namespace("world_monitor") == "world_monitor"
    assert ex.namespace("my-db.name") == "my_db_name"
    assert ex.namespace("--weird--") == "weird"
    assert ex.target_prefix(Path("/x/workflow_state.db")) == "workflow_state__"


def test_create_table_gets_prefix_and_if_not_exists():
    table_map = {"findings": "wm__findings"}
    out = ex.rewrite_ddl("CREATE TABLE findings (id INTEGER PRIMARY KEY, title TEXT)", table_map, {})
    assert out.startswith("CREATE TABLE IF NOT EXISTS wm__findings(")
    assert "id INTEGER PRIMARY KEY" in out and "title TEXT" in out


def test_quoted_identifiers_and_references_are_retargeted():
    table_map = {"claims": "ss__claims", "papers": "ss__papers"}
    ddl = 'CREATE TABLE "claims" ("id" TEXT PRIMARY KEY, "paper_id" TEXT REFERENCES "papers"("id"))'
    out = ex.rewrite_ddl(ddl, table_map, {})
    assert "CREATE TABLE IF NOT EXISTS ss__claims" in out
    assert "REFERENCES ss__papers" in out


def test_unique_index_is_retargeted():
    table_map = {"findings": "wm__findings"}
    index_map = {"idx_title": "wm__idx_title"}
    out = ex.rewrite_ddl("CREATE UNIQUE INDEX idx_title ON findings (title)", table_map, index_map)
    assert out == "CREATE UNIQUE INDEX IF NOT EXISTS wm__idx_title ON wm__findings (title)"


def test_partial_index_keeps_its_where_clause():
    out = ex.rewrite_ddl(
        "CREATE INDEX idx_a ON t (a) WHERE a IS NOT NULL",
        {"t": "ns__t"}, {"idx_a": "ns__idx_a"},
    )
    assert out.endswith(" WHERE a IS NOT NULL")


def test_virtual_table_is_skipped():
    assert ex.rewrite_ddl("CREATE VIRTUAL TABLE fts USING fts5(body)", {"fts": "x__fts"}, {}) is None


def test_unmapped_object_is_skipped():
    assert ex.rewrite_ddl("CREATE TABLE other (a INT)", {"findings": "x__findings"}, {}) is None
