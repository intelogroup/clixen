"""Local archive for papers found by query-based pubmed watch automations
(user_automation.py's `action_type == "email"` + `query` branch). Those
automations only kept seen_hashes/seen_pmids for dedup and threw the actual
paper away after sending the email — this table keeps the content so it can
be queried/reused later (e.g. "Anti-Aging PubMed Watch").
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_DB_PATH = Path(__file__).parent / "pubmed_watch.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance_id TEXT NOT NULL,
            task_name TEXT NOT NULL,
            pmid TEXT,
            title TEXT NOT NULL,
            url TEXT,
            published_date TEXT,
            snippet TEXT,
            found_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_papers_task ON papers(task_name)"
    )
    return conn


def save_papers(instance_id: str, task_name: str, papers: list[dict]) -> None:
    if not papers:
        return
    conn = _conn()
    with conn:
        conn.executemany(
            """INSERT INTO papers (instance_id, task_name, pmid, title, url, published_date, snippet)
               VALUES (:instance_id, :task_name, :pmid, :title, :url, :published_date, :snippet)""",
            [
                {
                    "instance_id": instance_id,
                    "task_name": task_name,
                    "pmid": p.get("pmid", ""),
                    "title": p.get("title", ""),
                    "url": p.get("url", ""),
                    "published_date": p.get("published_date", ""),
                    "snippet": p.get("snippet", ""),
                }
                for p in papers
            ],
        )
    conn.close()


def list_papers(task_name: str | None = None, limit: int = 500) -> list[dict]:
    conn = _conn()
    conn.row_factory = sqlite3.Row
    if task_name:
        rows = conn.execute(
            "SELECT * FROM papers WHERE task_name = ? ORDER BY found_at DESC LIMIT ?",
            (task_name, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM papers ORDER BY found_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
