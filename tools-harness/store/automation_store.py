"""
Persistent automation presets.

Stores user-tuned defaults for the automation starter pack so inputs survive
browser reloads and app restarts.
"""

from __future__ import annotations

import json
import sqlite3
from store import dbclose
from pathlib import Path

DB_PATH = Path(__file__).parent / "automation_presets.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS automation_presets (
    automation_id TEXT PRIMARY KEY,
    params_json   TEXT NOT NULL DEFAULT '{}',
    updated_at    TEXT DEFAULT (datetime('now'))
)
"""


def _conn() -> sqlite3.Connection:
    conn = dbclose.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _conn() as conn:
        conn.execute(_CREATE)


def get_preset(automation_id: str) -> dict:
    with _conn() as conn:
        row = conn.execute(
            "SELECT params_json FROM automation_presets WHERE automation_id = ?",
            (automation_id,),
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["params_json"])
        except Exception:
            return {}


def save_preset(automation_id: str, params: dict) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO automation_presets (automation_id, params_json, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(automation_id) DO UPDATE SET
                params_json = excluded.params_json,
                updated_at = datetime('now')
            """,
            (automation_id, json.dumps(params, sort_keys=True)),
        )


def list_presets() -> dict[str, dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT automation_id, params_json FROM automation_presets").fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            try:
                result[row["automation_id"]] = json.loads(row["params_json"])
            except Exception:
                result[row["automation_id"]] = {}
        return result
