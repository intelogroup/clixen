"""Handler: nightly_backup

Two jobs, one alert path:

1. **Consistent local snapshots** — `data/backups/<YYYY-MM-DD>/<store>.db` with
   N-day rotation. This is the restore story: the Turso replica keeps no
   history, so it cannot recover a deleted or corrupted local store.
2. **Incremental Turso refresh** — reuses each table's manifest hash to skip
   unchanged tables, so a normal night pushes a few hundred rows instead of all
   ~26k, and the replica stays current for the stores that are snapshot-only
   (the three scouts already mirror live).

Returns `{"success": bool, ...}`; `jobs/worker.py` telegrams on `success=False`,
so a night of silent failures becomes a message instead of quiet drift.

instance["config"] keys:
  keep_snapshots  int  — day-directories to retain (default 7)
  skip_snapshots  bool — replica refresh only
  skip_turso      bool — snapshots only (offline machine, no credentials)
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

_TOOLS_HARNESS = Path(__file__).resolve().parents[2]
_SCRIPTS = _TOOLS_HARNESS / "scripts"
_MODULES: dict[str, object] = {}


def _load_script(name: str):
    """Import scripts/<name>.py as a module (they are not a package)."""
    if name in _MODULES:
        return _MODULES[name]
    path = _SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_clixen_script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    _MODULES[name] = module
    return module


def handle(instance: dict) -> dict:
    cfg = (instance or {}).get("config") or {}
    keep = int(cfg.get("keep_snapshots", 7) or 7)
    result: dict = {"success": True, "snapshots": None, "turso": None}

    if not cfg.get("skip_snapshots"):
        try:
            from store.db_inventory import discover_dbs

            snapshots = _load_script("db_snapshot")
            stats = snapshots.run_snapshots(discover_dbs([]), keep=keep)
            result["snapshots"] = {
                k: stats.get(k) for k in ("date", "dir", "databases", "bytes", "removed")
            }
            if not stats.get("success"):
                result["success"] = False
                result["error"] = f"snapshots: {stats.get('error', 'unknown')}"
        except Exception as exc:  # noqa: BLE001 — handler contract: never raise
            _log.exception("nightly_backup: snapshot step failed")
            result["success"] = False
            result["error"] = f"snapshots crashed: {type(exc).__name__}: {exc}"

    if not cfg.get("skip_turso"):
        try:
            from store.db_inventory import discover_dbs

            exporter = _load_script("turso_export_all")
            # science_scout.db is skipped by the exporter: it mirrors live.
            totals = exporter.run_export(
                discover_dbs([], include_live_mirrored=False),
                incremental=True,
                quiet=True,
            )
            result["turso"] = {
                k: totals.get(k) for k in
                ("databases", "tables", "rows", "skipped", "raced", "rebuilt",
                 "mismatch", "unverified")
            }
            if totals.get("added_columns"):
                result["turso"]["added_columns"] = totals["added_columns"]
            if not totals.get("success"):
                result["success"] = False
                result["error"] = result.get("error") or f"turso: {totals.get('error', 'unknown')}"
        except Exception as exc:  # noqa: BLE001
            _log.exception("nightly_backup: turso step failed")
            result["success"] = False
            result["error"] = result.get("error") or f"turso crashed: {type(exc).__name__}: {exc}"

    _log.info(
        "nightly_backup: success=%s snapshots=%s turso=%s",
        result["success"], result["snapshots"], result["turso"],
    )
    return result
