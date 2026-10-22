"""The SQLite catalog for the tsdata lake.

One row per (dataset, series) plus a values table. Wide-ish layout: one row
per observation, with the series identity carried on every row. That is
deliberately denormalised — the alternative (one row per series with an array
column) makes it impossible to slice by time in SQLite, and these tables are
routinely queried as "give me everything in window W".

Three tables:

* ``datasets`` — one row per source dataset, recording provenance (URL,
  retrieval time, license) so a later audit can tell what came from where.
* ``series``  — metadata for each individual series (id, dataset, domain,
  frequency, start/end, length). This is what forecasting code iterates over.
* ``observations`` — the values themselves.

The store is opened through ``store.dbclose.connect`` so it inherits the
repo's WAL + busy_timeout defaults; the worker process and interactive
notebooks can then both write without "database is locked".
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from store.dbclose import connect

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id   TEXT NOT NULL,
    source       TEXT NOT NULL,
    domain       TEXT,
    title        TEXT,
    source_url   TEXT,
    license      TEXT,
    freq         TEXT,
    n_series     INTEGER,
    n_obs        INTEGER,
    first_ts     TEXT,
    last_ts      TEXT,
    acquired_at  REAL,
    meta_json    TEXT,
    PRIMARY KEY(dataset_id, source)
);

CREATE TABLE IF NOT EXISTS series (
    series_id   TEXT NOT NULL,
    dataset_id  TEXT NOT NULL,
    source      TEXT NOT NULL,
    domain      TEXT,
    freq        TEXT,
    title       TEXT,
    n_obs       INTEGER,
    first_ts    TEXT,
    last_ts     TEXT,
    meta_json   TEXT,
    PRIMARY KEY(series_id, source)
);

CREATE TABLE IF NOT EXISTS observations (
    series_id TEXT NOT NULL,
    ts        TEXT NOT NULL,          -- ISO-8601 UTC
    value     REAL,
    PRIMARY KEY(series_id, ts)
);

CREATE INDEX IF NOT EXISTS idx_obs_ts      ON observations(ts);
CREATE INDEX IF NOT EXISTS idx_obs_series ON observations(series_id, ts);
CREATE INDEX IF NOT EXISTS idx_series_ds   ON series(dataset_id);
CREATE INDEX IF NOT EXISTS idx_series_dom  ON series(domain);
"""


@dataclass
class DatasetRef:
    """Metadata for one harvested dataset."""

    dataset_id: str
    source: str
    domain: str = ""
    title: str = ""
    source_url: str = ""
    license: str = ""
    freq: str = ""
    n_series: int = 0
    n_obs: int = 0
    first_ts: str = ""
    last_ts: str = ""
    acquired_at: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> tuple:
        return (
            self.dataset_id,
            self.source,
            self.domain,
            self.title,
            self.source_url,
            self.license,
            self.freq,
            self.n_series,
            self.n_obs,
            self.first_ts,
            self.last_ts,
            self.acquired_at,
            json.dumps(self.meta, sort_keys=True, default=str),
        )


class Catalog:
    """SQLite catalog of everything harvested so far."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    def upsert_dataset(self, ds: DatasetRef) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO datasets
                   (dataset_id, source, domain, title, source_url, license, freq,
                    n_series, n_obs, first_ts, last_ts, acquired_at, meta_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ds.to_row(),
            )

    def upsert_series(self, rows: Iterable[tuple]) -> int:
        """rows: (series_id, dataset_id, source, domain, freq, title, n_obs,
        first_ts, last_ts, meta_json)"""
        payload = list(rows)
        if not payload:
            return 0
        with connect(self.db_path) as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO series
                   (series_id, dataset_id, source, domain, freq, title, n_obs,
                    first_ts, last_ts, meta_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                payload,
            )
        return len(payload)

    def insert_observations(self, rows: Iterable[tuple]) -> int:
        """rows: (series_id, ts, value)"""
        payload = list(rows)
        if not payload:
            return 0
        with connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO observations(series_id, ts, value) VALUES (?,?,?)",
                payload,
            )
        return len(payload)

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def datasets(self) -> list[dict]:
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                "SELECT * FROM datasets ORDER BY source, dataset_id")]

    def series(self, dataset_id: str | None = None, domain: str | None = None) -> list[dict]:
        q = "SELECT * FROM series"
        clauses, params = [], []
        if dataset_id:
            clauses.append("dataset_id = ?")
            params.append(dataset_id)
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY series_id"
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(q, params)]

    def observations(self, series_id: str) -> list[tuple[str, float]]:
        with connect(self.db_path) as conn:
            return [(r[0], r[1]) for r in conn.execute(
                "SELECT ts, value FROM observations WHERE series_id = ? ORDER BY ts",
                (series_id,))]

    def stats(self) -> dict:
        with connect(self.db_path) as conn:
            g = lambda q: conn.execute(q).fetchone()[0]
            return {
                "datasets": g("SELECT COUNT(*) FROM datasets"),
                "series": g("SELECT COUNT(*) FROM series"),
                "observations": g("SELECT COUNT(*) FROM observations"),
                "domains": g("SELECT COUNT(DISTINCT domain) FROM series WHERE domain IS NOT NULL"),
                "sources": g("SELECT COUNT(DISTINCT source) FROM datasets"),
            }

