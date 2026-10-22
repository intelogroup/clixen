"""The lake: harvest sources -> write to the catalog.

`Lake` is the façade the rest of the code talks to. It owns a `Catalog`
(SQLite) and a raw-response cache directory, and exposes:

* `harvest(key)`      — pull one source, normalise, persist.
* `harvest_all()`     — pull every registered source.
* `series(...)`       — query the catalog for series metadata.
* `frame(series_id)`  — pull one series back as a pandas DataFrame.
* `panel(domain)`     — a tidy multi-series panel for backtesting.

The important detail: harvesting is *idempotent*. Series ids are stable and
observations are keyed by (series_id, ts), so re-running a harvest updates
values in place rather than duplicating them. That matters because public
APIs revise history — the ECB restates FX rates, the World Bank rebases
national accounts, and USGS reprocesses streamflow. A lake that appends
would silently double-count the revised history.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

from .catalog import Catalog, DatasetRef
from .http import Fetcher
from .sources import SOURCES, Series, Source, get_source

#: Where the lake lives. Under tools-harness/data (gitignored) so it never
#: lands in the repo, and next to the other runtime stores.
DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "data" / "tsdata"


class Lake:
    """A local lake of harvested time series."""

    def __init__(self, root: str | Path = DEFAULT_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog = Catalog(self.root / "catalog.db")
        self.cache_dir = self.root / "raw"
        self.fetcher = Fetcher(self.cache_dir)

    # ------------------------------------------------------------------
    # harvest
    # ------------------------------------------------------------------
    def harvest(self, key: str, *, force: bool = False) -> dict[str, Any]:
        """Harvest one registered source into the catalog."""
        src = get_source(key)
        if not src.available():
            return {"source": key, "status": "skipped", "reason": "missing API key"}
        started = time.time()
        try:
            series_list = list(src.harvest(self.fetcher))
        except Exception as e:  # a dead source must not kill the whole run
            return {
                "source": key, "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "seconds": round(time.time() - started, 2),
            }
        return self._persist(src, series_list, started, force=force)

    def harvest_all(self, keys: Iterable[str] | None = None, *, force: bool = False) -> list[dict]:
        targets = list(keys) if keys else [s.key for s in SOURCES]
        results = []
        for k in targets:
            r = self.harvest(k, force=force)
            results.append(r)
            print(
                f"  {r['source']:24s} {r['status']:8s} "
                f"series={r.get('n_series', 0):6d} obs={r.get('n_obs', 0):9d} "
                f"{r.get('error', '')}",
                flush=True,
            )
        return results

    def _persist(self, src: Source, series_list: list[Series], started: float,
                 force: bool = False) -> dict[str, Any]:
        series_list = [s for s in series_list if s.points]
        if not series_list:
            return {"source": src.key, "status": "empty", "seconds": round(time.time() - started, 2)}

        all_obs: list[tuple[str, str, float]] = []
        meta_rows: list[tuple] = []
        first_ts, last_ts = "", ""
        for s in series_list:
            for ts, val in s.points:
                all_obs.append((s.series_id, ts, val))
            stamps = [p[0] for p in s.points]
            meta_rows.append((
                s.series_id,
                src.key,
                src.key,
                src.domain,
                src.freq,
                s.title,
                len(s.points),
                stamps[0],
                stamps[-1],
                json.dumps(s.meta, sort_keys=True, default=str),
            ))
            if not first_ts or stamps[0] < first_ts:
                first_ts = stamps[0]
            if not last_ts or stamps[-1] > last_ts:
                last_ts = stamps[-1]

        self.catalog.upsert_series(meta_rows)
        self.catalog.insert_observations(all_obs)
        self.catalog.upsert_dataset(DatasetRef(
            dataset_id=src.key,
            source=src.key,
            domain=src.domain,
            title=src.name,
            source_url=src.source_url,
            license=src.license,
            freq=src.freq,
            n_series=len(series_list),
            n_obs=len(all_obs),
            first_ts=first_ts,
            last_ts=last_ts,
            acquired_at=time.time(),
            meta={"notes": src.notes, "key": src.key},
        ))
        return {
            "source": src.key,
            "status": "ok",
            "n_series": len(series_list),
            "n_obs": len(all_obs),
            "first_ts": first_ts,
            "last_ts": last_ts,
            "seconds": round(time.time() - started, 2),
        }

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        return self.catalog.stats()

    def series(self, **kw: Any) -> list[dict]:
        return self.catalog.series(**kw)

    def datasets(self) -> list[dict]:
        return self.catalog.datasets()

    def frame(self, series_id: str):
        """One series as a pandas DataFrame indexed by timestamp."""
        import pandas as pd
        rows = self.catalog.observations(series_id)
        if not rows:
            raise KeyError(f"no such series: {series_id}")
        df = pd.DataFrame(rows, columns=["ts", "value"])
        df["ts"] = pd.to_datetime(df["ts"], utc=True, format="mixed")
        return df.set_index("ts").sort_index()

    def panel(self, domain: str | None = None, min_obs: int = 30):
        """A tidy panel: one row per (series, ts)."""
        import pandas as pd
        rows = []
        for meta in self.catalog.series(domain=domain):
            if (meta.get("n_obs") or 0) < min_obs:
                continue
            for ts, val in self.catalog.observations(meta["series_id"]):
                rows.append((meta["series_id"], ts, val))
        df = pd.DataFrame(rows, columns=["series_id", "ts", "value"])
        if df.empty:
            return df
        df["ts"] = pd.to_datetime(df["ts"], utc=True, format="mixed")
        return df.sort_values(["series_id", "ts"]).reset_index(drop=True)

    def close(self) -> None:
        self.fetcher.close()

    def __enter__(self) -> "Lake":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
