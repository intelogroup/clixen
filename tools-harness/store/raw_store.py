"""
SQLite raw store — source of truth for all ingested content.

Rules:
- sha256 of content = primary key (dedup is automatic)
- content_hash tracks the file on disk — if file moves, hash still matches
- expires_at NULL = forever, unix ts = TTL deadline
- cleanup: on startup + lazy (each write purges expired rows)
- WAL mode on for concurrent reads from other processes
"""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "raw.db"

TTL = {
    "brave_search":     60 * 60 * 6,
    "context7_docs":    60 * 60 * 24 * 7,
    "ocr_pdf":          None,
    "excel":            None,
    "audio_transcript": None,
    "video_description":None,
    "manual":           None,
    "chat_summary":     None,
    "llm_response":     None,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_docs (
    id           TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    source       TEXT NOT NULL,
    file_path    TEXT,
    query        TEXT,
    url          TEXT,
    content      TEXT NOT NULL,
    metadata     TEXT,
    created_at   REAL NOT NULL,
    expires_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_source     ON raw_docs(source);
CREATE INDEX IF NOT EXISTS idx_expires    ON raw_docs(expires_at);
CREATE INDEX IF NOT EXISTS idx_content_hash ON raw_docs(content_hash);
CREATE INDEX IF NOT EXISTS idx_file_path  ON raw_docs(file_path);
"""


def _content_id(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]

def _file_hash(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except Exception:
        return None


class RawStore:
    def __init__(self, db_path: str = str(DB_PATH)):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init()

    def _init(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
        self._cleanup_expired()  # startup cleanup

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── Write ──────────────────────────────────────────────────────────────

    def store(
        self,
        content: str,
        source: str,
        file_path: str = "",
        query: str = "",
        url: str = "",
        metadata: dict = None,
        ttl_override: Optional[float] = None,
    ) -> tuple[str, bool]:
        """
        Store content. Returns (id, was_new).
        Dedup: same content sha256 = no-op, returns existing id.
        """
        cid = _content_id(content)
        content_hash = _file_hash(file_path) if file_path else cid

        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM raw_docs WHERE id = ?", (cid,)
            ).fetchone()
            if existing:
                return cid, False

            ttl = ttl_override if ttl_override is not None else TTL.get(source)
            expires_at = (time.time() + ttl) if ttl else None

            conn.execute(
                """INSERT INTO raw_docs
                   (id, content_hash, source, file_path, query, url,
                    content, metadata, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid, content_hash, source,
                    file_path or "", query or "", url or "",
                    content, json.dumps(metadata or {}),
                    time.time(), expires_at,
                ),
            )

        # Lazy cleanup on every write
        self._cleanup_expired()
        return cid, True

    # ── Read ───────────────────────────────────────────────────────────────

    def get(self, doc_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM raw_docs WHERE id = ?", (doc_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_file(self, file_path: str) -> Optional[dict]:
        """Look up by file path first, then verify content hash matches."""
        fh = _file_hash(file_path)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM raw_docs WHERE file_path = ? OR content_hash = ?",
                (file_path, fh),
            ).fetchone()
        return dict(row) if row else None

    def is_indexed(self, file_path: str) -> bool:
        """True if file is already in raw store (by path or hash)."""
        return self.get_by_file(file_path) is not None

    def stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM raw_docs").fetchone()[0]
            by_source = conn.execute(
                "SELECT source, COUNT(*) as n FROM raw_docs GROUP BY source"
            ).fetchall()
            expiring = conn.execute(
                "SELECT COUNT(*) FROM raw_docs WHERE expires_at IS NOT NULL"
            ).fetchone()[0]
        return {
            "total":     total,
            "expiring":  expiring,
            "permanent": total - expiring,
            "by_source": {r["source"]: r["n"] for r in by_source},
        }

    # ── Cleanup ────────────────────────────────────────────────────────────

    def _cleanup_expired(self):
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM raw_docs WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
