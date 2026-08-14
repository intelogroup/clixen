"""Small local working-set cache for document evidence per chat session."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from store import dbclose


_DB_PATH = Path(__file__).parent.parent / "store" / "document_session.db"


def _conn():
    conn = dbclose.connect(str(_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS evidence ("
        "session_id TEXT NOT NULL, workspace TEXT NOT NULL, source TEXT NOT NULL, "
        "fingerprint TEXT NOT NULL, locator TEXT NOT NULL, text TEXT NOT NULL, "
        "last_used TEXT DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY(session_id, workspace, source, fingerprint, locator, text))"
    )
    return conn


def _file_fingerprint(source: str) -> str:
    path = Path(source)
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()
    except (OSError, ValueError):
        # Provider results and synthetic evidence are versioned by content.
        return hashlib.sha256(source.encode()).hexdigest()


def remember(session_id: str | None, workspace: str | None, chunks: list[dict]) -> int:
    """Save evidence chunks for a session; reject sources outside workspace."""
    if not session_id or not workspace:
        return 0
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        return 0
    rows = []
    for chunk in chunks:
        source = str(chunk.get("source", ""))
        try:
            source_path = Path(source).expanduser().resolve()
            source_path.relative_to(root)
        except (OSError, ValueError):
            continue
        if not source_path.is_file():
            continue
        rows.append((str(session_id), str(root), str(source_path), _file_fingerprint(str(source_path)),
                     str(chunk.get("locator", "document")), str(chunk.get("text", ""))))
    if not rows:
        return 0
    with _conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO evidence(session_id, workspace, source, fingerprint, locator, text) "
            "VALUES(?, ?, ?, ?, ?, ?)", rows,
        )
    return len(rows)


def recall(session_id: str | None, workspace: str | None, limit: int = 8) -> list[dict[str, str]]:
    """Return current-session chunks, invalidating changed/deleted source files."""
    if not session_id or not workspace:
        return []
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        return []
    with _conn() as conn:
        rows = conn.execute(
            "SELECT source, fingerprint, locator, text FROM evidence "
            "WHERE session_id = ? AND workspace = ? ORDER BY last_used DESC LIMIT ?",
            (str(session_id), str(root), max(1, min(int(limit) * 4, 64))),
        ).fetchall()
        current = []
        stale = []
        for source, expected, locator, text in rows:
            if not Path(source).is_file() or _file_fingerprint(source) != expected:
                stale.append((str(session_id), str(root), source, expected, locator, text))
                continue
            current.append({"source": source, "locator": locator, "text": text})
        if stale:
            conn.executemany(
                "DELETE FROM evidence WHERE session_id=? AND workspace=? AND source=? AND fingerprint=? AND locator=? AND text=?",
                stale,
            )
        if current:
            conn.execute(
                "UPDATE evidence SET last_used=CURRENT_TIMESTAMP WHERE session_id=? AND workspace=?",
                (str(session_id), str(root)),
            )
    return current[: max(1, min(int(limit), 20))]


def forget_source(source: str) -> int:
    path = str(Path(source).expanduser().resolve())
    with _conn() as conn:
        result = conn.execute("DELETE FROM evidence WHERE source = ?", (path,))
        return result.rowcount
